"""The MCP tools, exercised end to end through the SDK's in-memory client."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from mcp import Client

from brand_kit_mcp.server import EMBED_MAX_BYTES, build_server

from .conftest import PNG_1x1

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {
    "list_brands",
    "get_brand",
    "get_palette",
    "get_typography",
    "get_logos",
    "search_assets",
    "get_asset",
    "export_brand_json",
    "export_tokens_css",
    "generate_brand_kit",
}


@pytest.fixture
def server(catalog):
    return build_server(catalog)


async def call(server, name: str, args: dict | None = None):
    async with Client(server) as client:
        return await client.call_tool(name, args or {})


async def test_tools_are_listed_and_read_only(server):
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False


async def test_list_brands(server):
    result = await call(server, "list_brands")
    brands = {b["slug"]: b for b in result.structured_content["brands"]}
    assert set(brands) == {"tidewren", "tidewren-trail"}
    assert brands["tidewren"]["children"] == ["tidewren-trail"]
    assert brands["tidewren"]["assets"] == 5  # quarantined one is not counted
    assert brands["tidewren-trail"]["parent"] == "tidewren"


async def test_get_brand(server):
    data = (await call(server, "get_brand", {"brand": "tidewren-trail"})).structured_content
    assert data["name"] == "Tidewren Trail"
    tokens = {t["name"]: t for t in data["tokens"]}
    assert tokens["color.accent"]["value"] == "#5B8C3A"
    assert "inherited_from" not in tokens["color.accent"]
    assert tokens["color.primary"]["inherited_from"] == "tidewren"
    assert data["guidelines"][0]["inherited_from"] == "tidewren"


async def test_unknown_brand_is_a_tool_error(server):
    result = await call(server, "get_palette", {"brand": "nope"})
    assert result.is_error
    assert "available brands: tidewren, tidewren-trail" in result.content[0].text


async def test_get_palette(server):
    data = (await call(server, "get_palette", {"brand": "tidewren"})).structured_content
    colors = {c["name"]: c for c in data["colors"]}
    assert colors["color.primary"]["rgb"] == [15, 76, 92]
    assert colors["color.primary"]["description"].startswith("Deep tide teal")
    assert colors["color.overlay"]["alpha"] == 0.8
    assert colors["color.focus"]["hex"] == "#FFAA44"


async def test_get_typography(server):
    data = (await call(server, "get_typography", {"brand": "tidewren"})).structured_content
    assert data["families"]["font.family.heading"] == ["Fraunces", "Georgia", "serif"]
    assert data["families"]["font.family.body"] == ["Inter", "system-ui", "sans-serif"]
    assert any(t["name"] == "font.weight.bold" for t in data["tokens"])


async def test_get_logos(server):
    data = (await call(server, "get_logos", {"brand": "tidewren"})).structured_content
    ids = [logo["id"] for logo in data["logos"]]
    assert "legacy-mark" not in ids
    assert set(ids) == {"logo-horizontal", "logomark", "logomark-png", "icon-wave"}
    assert data["pending_review"] == 0


async def test_search_assets_with_facets(server):
    data = (await call(server, "search_assets", {"query": "square"})).structured_content
    assert data["total"] == 3
    assert data["facets"]["brand"] == {"tidewren": 2, "tidewren-trail": 1}
    data = (await call(server, "search_assets", {"brand": "tidewren", "category": "pattern"})).structured_content
    (only,) = data["results"]
    assert only["id"] == "pattern-tide" and only["approved"] is False and "notice" in only
    data = (await call(server, "search_assets", {"brand": "tidewren", "approved_only": True})).structured_content
    assert all(r["approved"] for r in data["results"])
    data = (await call(server, "search_assets", {"tag": "legacy"})).structured_content
    assert data["total"] == 0  # the only legacy asset is quarantined
    data = (await call(server, "search_assets", {"limit": 2})).structured_content
    assert data["returned"] == 2 and data["total"] == 6


async def test_get_asset_png_is_embedded(server, examples_dir: Path):
    result = await call(server, "get_asset", {"brand": "tidewren", "asset_id": "logomark-png"})
    assert not result.is_error
    meta, image = result.content
    assert json.loads(meta.text)["embedded"] == "image"
    assert image.type == "image" and image.mime_type == "image/png"
    expected = (examples_dir / "tidewren" / "assets" / "logomark-256.png").read_bytes()
    assert base64.b64decode(image.data) == expected


async def test_get_asset_svg_is_text(server):
    result = await call(server, "get_asset", {"brand": "tidewren", "asset_id": "logomark"})
    meta, svg = result.content
    assert json.loads(meta.text)["embedded"] == "svg-text"
    assert svg.type == "text" and svg.text.lstrip().startswith("<svg")


async def test_get_asset_quarantined_is_not_found(server):
    result = await call(server, "get_asset", {"brand": "tidewren", "asset_id": "legacy-mark"})
    assert result.is_error
    assert "unknown asset 'legacy-mark'" in result.content[0].text


async def test_large_image_is_not_embedded(make_brand):
    big = PNG_1x1 + b"\0" * (EMBED_MAX_BYTES + 1)
    make_brand(
        "acme",
        "name: Acme\nassets:\n  - {id: big, file: big.png, name: Big, category: photo, status: approved}\n",
        {"big.png": big},
    )
    result = await call(build_server(make_brand.load()), "get_asset", {"brand": "acme", "asset_id": "big"})
    assert len(result.content) == 1
    meta = json.loads(result.content[0].text)
    assert meta["embedded"] is None and "2 MB" in meta["notice_content"]


async def test_review_asset_is_marked(make_brand):
    make_brand(
        "acme",
        "name: Acme\nassets:\n  - {id: draft, file: d.png, name: Draft, category: logo, status: review}\n",
        {"d.png": PNG_1x1},
    )
    server = build_server(make_brand.load())
    data = (await call(server, "get_logos", {"brand": "acme"})).structured_content
    assert data["pending_review"] == 1 and data["logos"][0]["approved"] is False
    result = await call(server, "get_asset", {"brand": "acme", "asset_id": "draft"})
    assert json.loads(result.content[0].text)["approved"] is False


async def test_export_brand_json(server):
    data = (await call(server, "export_brand_json", {"brand": "tidewren"})).structured_content
    assert data["tokens"]["color"]["primary"]["$root"]["$type"] == "color"
    assert data["omitted"][0]["name"] == "font.style.tagline"


async def test_export_tokens_css(server):
    result = await call(server, "export_tokens_css", {"brand": "tidewren", "prefix": "tw"})
    assert "--tw-color-primary: #0F4C5C;" in result.content[0].text
    bad = await call(server, "export_tokens_css", {"brand": "tidewren", "prefix": "Bad!"})
    assert bad.is_error


async def test_generate_brand_kit(server):
    result = await call(server, "generate_brand_kit", {"brand": "tidewren", "kind": "presentation"})
    assert result.content[0].text.startswith("# Tidewren brand kit (presentation)")


async def test_resources(server):
    async with Client(server) as client:
        templates = (await client.list_resource_templates()).resource_templates
        assert {t.uri_template for t in templates} == {
            "brand://{slug}/brand.json",
            "brand://{slug}/tokens.css",
            "brand://{slug}/kit.md",
        }
        doc = await client.read_resource("brand://tidewren/brand.json")
        assert json.loads(doc.contents[0].text)["brand"]["slug"] == "tidewren"
        css = await client.read_resource("brand://tidewren-trail/tokens.css")
        assert "--color-accent: #5B8C3A;" in css.contents[0].text
