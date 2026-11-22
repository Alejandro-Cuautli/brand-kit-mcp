"""The MCP server: read-only tools and resources over a :class:`~brand_kit_mcp.catalog.Catalog`."""

from __future__ import annotations

import base64
import json
from typing import Annotated, Any, Literal, Union

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from . import __version__, dtcg
from .catalog import Asset, Catalog, NotFound
from .css import export_css
from .dtcg import convert_tokens
from .kit import generate_kit
from .media import EMBEDDABLE_MIME

#: Largest image embedded in a tool result (MCP clients choke on bigger payloads).
EMBED_MAX_BYTES = 2 * 1024 * 1024
SEARCH_LIMIT_MAX = 200

INSTRUCTIONS = """\
brand-kit-mcp serves the identity of one or more brands: palette, typography, spacing, logos
and usage rules. Everything is read-only.

Typical flow:
1. `list_brands` to find the brand slug.
2. `generate_brand_kit` (kind=document or presentation) for a ready-to-follow summary, or
   `get_palette` / `get_typography` / `get_logos` for specific parts.
3. `get_asset` to fetch a logo (raster images are embedded, SVG is returned as text).
4. `export_brand_json` (DTCG design tokens) or `export_tokens_css` when building UI.

Rules: use only the values returned here, never invent brand colors or redraw logos, and do not
use assets whose `approved` field is false in final deliverables.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

BrandArg = Annotated[str, Field(description="Brand slug (or exact brand name) from list_brands.")]


def build_server(catalog: Catalog) -> MCPServer:
    server = MCPServer(
        name="brand-kit-mcp",
        title="Brand Kit",
        version=__version__,
        instructions=INSTRUCTIONS,
    )

    def brand_or_error(ref: str):
        try:
            return catalog.get(ref)
        except NotFound as exc:
            raise ToolError(str(exc)) from None

    @server.tool(annotations=READ_ONLY)
    def list_brands() -> dict[str, Any]:
        """List every brand with its slug, description, parent brand and asset counts."""
        return {"brands": [b.summary() for b in catalog]}

    @server.tool(annotations=READ_ONLY)
    def get_brand(brand: BrandArg) -> dict[str, Any]:
        """Full brand profile: description, parent, guidelines, every token and the usable assets."""
        b = brand_or_error(brand)
        return {
            **b.summary(),
            "guidelines": [
                {"text": text, **({"inherited_from": src} if src != b.slug else {})} for text, src in b.guidelines
            ],
            "tokens": [t.as_dict(b.slug) for t in b.tokens],
            "assets": [a.summary() for a in b.visible_assets()],
        }

    @server.tool(annotations=READ_ONLY)
    def get_palette(brand: BrandArg) -> dict[str, Any]:
        """Brand colors (`color.*` tokens) as hex, with RGB components and alpha when present."""
        b = brand_or_error(brand)
        _, exported, _ = convert_tokens(b.tokens_with_prefix("color."))
        converted = {t.name: t.value for t in exported}
        colors = []
        for token in b.tokens_with_prefix("color."):
            row = token.as_dict(b.slug)
            value = converted.get(token.name)
            if value:
                row["hex"] = value["hex"]
                row["rgb"] = [round(c * 255) for c in value["components"]]
                if "alpha" in value:
                    row["alpha"] = value["alpha"]
            colors.append(row)
        return {"brand": b.slug, "name": b.name, "colors": colors}

    @server.tool(annotations=READ_ONLY)
    def get_typography(brand: BrandArg) -> dict[str, Any]:
        """Font families, weights, sizes and line heights (`font.*` tokens)."""
        b = brand_or_error(brand)
        fonts = b.tokens_with_prefix("font.")
        families = {}
        for token in fonts:
            if token.name.startswith("font.family"):
                value = token.value
                stack = value if isinstance(value, list) else [p.strip().strip("'\"") for p in str(value).split(",")]
                families[token.name] = [s for s in stack if s]
        return {
            "brand": b.slug,
            "name": b.name,
            "families": families,
            "tokens": [t.as_dict(b.slug) for t in fonts],
        }

    @server.tool(annotations=READ_ONLY)
    def get_logos(brand: BrandArg) -> dict[str, Any]:
        """Logos, logomarks, wordmarks and icons of a brand. Items with approved=false are pending review."""
        b = brand_or_error(brand)
        logos = b.logos()
        return {
            "brand": b.slug,
            "logos": [a.summary() for a in logos],
            "approved": sum(1 for a in logos if a.approved),
            "pending_review": sum(1 for a in logos if not a.approved),
        }

    @server.tool(annotations=READ_ONLY)
    def search_assets(
        brand: Annotated[str | None, Field(description="Limit to one brand slug.")] = None,
        category: Annotated[
            str | None,
            Field(description="logo, logomark, wordmark, icon, illustration, pattern, photo, template, other"),
        ] = None,
        tag: Annotated[str | None, Field(description="Exact tag.")] = None,
        query: Annotated[str | None, Field(description="Free text over id, name, description and tags.")] = None,
        approved_only: Annotated[bool, Field(description="Exclude assets pending review.")] = False,
        limit: Annotated[int, Field(ge=1, le=SEARCH_LIMIT_MAX)] = 50,
    ) -> dict[str, Any]:
        """Search assets across brands. Returns results, the total and facets (brand, category, status, tag)."""
        try:
            found = catalog.assets(brand.strip() if brand and brand.strip() else None)
        except NotFound as exc:
            raise ToolError(str(exc)) from None
        if category:
            found = [a for a in found if a.category == category.strip().lower()]
        if tag:
            wanted = tag.strip().lower()
            found = [a for a in found if wanted in a.tags]
        if approved_only:
            found = [a for a in found if a.approved]
        if query:
            words = query.lower().split()
            found = [a for a in found if all(w in _haystack(a) for w in words)]
        return {
            "total": len(found),
            "returned": min(len(found), limit),
            "results": [a.summary() for a in found[:limit]],
            "facets": catalog.facets(found),
        }

    @server.tool(annotations=READ_ONLY)
    def get_asset(
        brand: BrandArg,
        asset_id: Annotated[str, Field(description="Asset id from get_logos or search_assets.")],
    ) -> list[Union[TextContent, ImageContent]]:
        """Asset metadata plus its content: PNG/JPEG/WebP up to 2 MB are embedded as an image, SVG as text."""
        try:
            asset = catalog.get_asset(brand, asset_id)
        except NotFound as exc:
            raise ToolError(str(exc)) from None
        meta = asset.summary()
        blocks: list[Union[TextContent, ImageContent]] = []
        extra: TextContent | ImageContent | None = None
        if asset.mime in EMBEDDABLE_MIME:
            data = catalog.read_asset(asset, EMBED_MAX_BYTES)
            if data is not None:
                extra = ImageContent(type="image", data=base64.b64encode(data).decode("ascii"), mime_type=asset.mime)
                meta["embedded"] = "image"
        elif asset.mime == "image/svg+xml":
            data = catalog.read_asset(asset, EMBED_MAX_BYTES)
            if data is not None:
                extra = TextContent(type="text", text=data.decode("utf-8", "replace"))
                meta["embedded"] = "svg-text"
        if extra is None:
            meta["embedded"] = None
            meta["notice_content"] = (
                "Content not embedded: format is not PNG/JPEG/WebP/SVG or the file is larger than 2 MB."
            )
        blocks.append(TextContent(type="text", text=json.dumps(meta, ensure_ascii=False, indent=2)))
        if extra is not None:
            blocks.append(extra)
        return blocks

    @server.tool(annotations=READ_ONLY)
    def export_brand_json(brand: BrandArg) -> dict[str, Any]:
        """Export the brand as brand.json: DTCG 2025.10 tokens, logos and guidelines.

        Tokens that cannot be expressed in DTCG are listed under `omitted` with a reason.
        """
        b = brand_or_error(brand)
        return dtcg.export_brand_json(catalog, b.slug)

    @server.tool(annotations=READ_ONLY)
    def export_tokens_css(
        brand: BrandArg,
        prefix: Annotated[
            str, Field(description="Optional variable prefix, e.g. 'acme' -> --acme-color-primary.")
        ] = "",
    ) -> str:
        """Export the brand tokens as CSS custom properties on :root."""
        b = brand_or_error(brand)
        try:
            return export_css(b, prefix)
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    @server.tool(annotations=READ_ONLY)
    def generate_brand_kit(
        brand: BrandArg,
        kind: Annotated[Literal["document", "presentation"], Field(description="What you are producing.")] = "document",
    ) -> str:
        """Markdown brand kit to follow while writing a document or building a presentation."""
        return generate_kit(brand_or_error(brand), kind)

    @server.resource(
        "brand://{slug}/brand.json",
        name="brand-json",
        description="The brand as brand.json (DTCG 2025.10 tokens, logos, guidelines).",
        mime_type="application/json",
    )
    def brand_json_resource(slug: str) -> str:
        return json.dumps(dtcg.export_brand_json(catalog, slug), ensure_ascii=False, indent=2)

    @server.resource(
        "brand://{slug}/tokens.css",
        name="tokens-css",
        description="The brand tokens as CSS custom properties.",
        mime_type="text/css",
    )
    def tokens_css_resource(slug: str) -> str:
        return export_css(catalog.get(slug))

    @server.resource(
        "brand://{slug}/kit.md",
        name="brand-kit",
        description="Markdown brand kit for documents.",
        mime_type="text/markdown",
    )
    def kit_resource(slug: str) -> str:
        return generate_kit(catalog.get(slug), "document")

    @server.custom_route("/health", methods=["GET"])
    async def health(_request):  # pragma: no cover - exercised in the HTTP smoke test
        from starlette.responses import JSONResponse

        return JSONResponse({"status": "ok"})

    return server


def _haystack(asset: Asset) -> str:
    return " ".join([asset.id, asset.name, asset.description, asset.category, asset.brand, *asset.tags]).lower()
