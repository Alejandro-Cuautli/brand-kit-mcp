"""Load and validate a directory of brands.

Layout::

    brands/
      acme/
        brand.yaml
        assets/logo.svg
      acme-labs/          # may declare `parent: acme`
        brand.yaml

Everything is read once at startup and kept in memory, except asset bytes, which are read on
demand and re-checked every time (path still inside the brand folder, size still within limits).
Outputs never contain absolute paths: assets are identified by brand slug + asset id, and files
are shown relative to their brand folder.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .media import EXTENSION_MIME, image_size, sniff_mime, svg_has_active_content
from .schema import LOGO_CATEGORIES, SLUG_RE, BrandSpec, Token

BRAND_FILE = "brand.yaml"
MAX_BRAND_FILE_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 50 * 1024 * 1024
MAX_BRANDS = 1000
MAX_PARENT_DEPTH = 8


class CatalogError(Exception):
    """Raised when the brands directory cannot be loaded. ``errors`` lists every problem."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


class NotFound(LookupError):
    """Unknown brand or asset (quarantined assets are reported as not found too)."""


@dataclass(frozen=True)
class Asset:
    brand: str
    id: str
    name: str
    category: str
    status: str
    tags: tuple[str, ...]
    description: str
    file: str
    size: int
    sha256: str
    mime: str
    width: int | None
    height: int | None
    active_content: bool = False

    @property
    def approved(self) -> bool:
        return self.status == "approved"

    @property
    def visible(self) -> bool:
        return self.status != "quarantined"

    @property
    def format(self) -> str:
        return Path(self.file).suffix.lower().lstrip(".")

    def summary(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "brand": self.brand,
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "approved": self.approved,
            "tags": list(self.tags),
            "file": self.file,
            "format": self.format,
            "mime": self.mime,
            "bytes": self.size,
            "sha256": self.sha256,
        }
        if self.width and self.height:
            data["width"], data["height"] = self.width, self.height
        if self.description:
            data["description"] = self.description
        if not self.approved:
            data["notice"] = "Pending review: do not use in final deliverables until it is approved."
        if self.active_content:
            data["warning"] = "SVG contains scripts or event handlers; sanitize before embedding in a web page."
        return data


@dataclass(frozen=True)
class ResolvedToken:
    name: str
    value: Any
    description: str
    source: str  # slug of the brand that defines it (differs from the brand when inherited)

    def as_dict(self, brand: str) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "value": self.value}
        if self.description:
            data["description"] = self.description
        if self.source != brand:
            data["inherited_from"] = self.source
        return data


@dataclass
class Brand:
    slug: str
    name: str
    description: str
    parent: str | None
    website: str | None
    root: Path = field(repr=False)
    own_tokens: list[Token]
    own_guidelines: list[str]
    assets: dict[str, Asset]
    children: list[str] = field(default_factory=list)
    tokens: list[ResolvedToken] = field(default_factory=list)
    guidelines: list[tuple[str, str]] = field(default_factory=list)  # (text, source slug)

    def visible_assets(self) -> list[Asset]:
        return [a for a in self.assets.values() if a.visible]

    def logos(self) -> list[Asset]:
        return [a for a in self.visible_assets() if a.category in LOGO_CATEGORIES]

    def tokens_with_prefix(self, *prefixes: str) -> list[ResolvedToken]:
        return [t for t in self.tokens if t.name.startswith(prefixes)]

    def summary(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "parent": self.parent,
            "children": list(self.children),
            "tokens": len(self.tokens),
            "assets": len(self.visible_assets()),
            "logos": len(self.logos()),
        }
        if self.website:
            data["website"] = self.website
        return data


class _NoAliasSafeLoader(yaml.SafeLoader):
    """``yaml.safe_load`` semantics, plus: anchors/aliases are rejected (no billion-laughs expansion)."""

    def compose_node(self, parent, index):  # type: ignore[override]
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise yaml.composer.ComposerError(
                None, None, "YAML aliases are not allowed in brand files", event.start_mark
            )
        return super().compose_node(parent, index)


def safe_load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_NoAliasSafeLoader)  # noqa: S506 - SafeLoader subclass


class Catalog:
    """All brands found in a directory."""

    def __init__(self, root: Path, brands: dict[str, Brand], warnings: list[str] | None = None):
        self.root = root
        self._brands = brands
        self.warnings = warnings or []

    # -- loading ---------------------------------------------------------------------------------

    @classmethod
    def load(cls, root: str | os.PathLike[str]) -> Catalog:
        root_path = Path(root).expanduser()
        if not root_path.exists():
            raise CatalogError([f"brands directory not found: {root_path}"])
        if not root_path.is_dir():
            raise CatalogError([f"not a directory: {root_path}"])
        root_real = root_path.resolve()
        errors: list[str] = []
        warnings: list[str] = []
        brands: dict[str, Brand] = {}

        for entry in sorted(root_path.iterdir(), key=lambda p: p.name):
            if entry.name.startswith((".", "_")):
                continue
            if entry.is_symlink():
                errors.append(f"{entry.name}/: symlinked brand folders are not allowed")
                continue
            if not entry.is_dir():
                continue
            if not SLUG_RE.match(entry.name):
                errors.append(
                    f"{entry.name}/: folder name is not a valid slug (lowercase letters, digits and '-', 1-64 chars)"
                )
                continue
            if len(brands) >= MAX_BRANDS:
                errors.append(f"more than {MAX_BRANDS} brands")
                break
            brand = _load_brand(entry, root_real, errors, warnings)
            if brand is not None:
                brands[brand.slug] = brand

        _resolve_inheritance(brands, errors)
        if errors:
            raise CatalogError(errors)
        if not brands:
            warnings.append(f"no brands found in {root_path} (expected <slug>/{BRAND_FILE})")
        return cls(root_path, brands, warnings)

    # -- queries ---------------------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._brands)

    def __iter__(self) -> Iterator[Brand]:
        return iter(self._brands[s] for s in sorted(self._brands))

    @property
    def slugs(self) -> list[str]:
        return sorted(self._brands)

    def get(self, ref: str) -> Brand:
        """Find a brand by slug, or by name (case-insensitive)."""
        ref = (ref or "").strip()
        if ref in self._brands:
            return self._brands[ref]
        lowered = ref.lower()
        for brand in self._brands.values():
            if brand.slug == lowered or brand.name.lower() == lowered:
                return brand
        available = ", ".join(self.slugs) or "none"
        raise NotFound(f"unknown brand {ref!r}; available brands: {available}")

    def assets(self, brand: str | None = None) -> list[Asset]:
        """Visible (non-quarantined) assets, optionally for one brand."""
        brands: Iterable[Brand] = [self.get(brand)] if brand else self
        return [a for b in brands for a in b.visible_assets()]

    def get_asset(self, brand: str, asset_id: str) -> Asset:
        record = self.get(brand)
        asset = record.assets.get((asset_id or "").strip())
        if asset is None or not asset.visible:
            raise NotFound(f"unknown asset {asset_id!r} in brand {record.slug!r}")
        return asset

    def read_asset(self, asset: Asset, max_bytes: int) -> bytes | None:
        """Read an asset's bytes if it is still safe to do so and not larger than ``max_bytes``."""
        brand = self._brands[asset.brand]
        try:
            real = (brand.root / asset.file).resolve(strict=True)
            real.relative_to(brand.root)
            if not real.is_file():
                return None
            size = real.stat().st_size
            if size > max_bytes or size > MAX_ASSET_BYTES:
                return None
            with real.open("rb") as handle:
                return handle.read(max_bytes + 1)[:max_bytes]
        except (OSError, ValueError):
            return None

    def facets(self, assets: Iterable[Asset]) -> dict[str, dict[str, int]]:
        assets = list(assets)
        return {
            "brand": dict(Counter(a.brand for a in assets).most_common()),
            "category": dict(Counter(a.category for a in assets).most_common()),
            "status": dict(Counter(a.status for a in assets).most_common()),
            "tag": dict(Counter(t for a in assets for t in a.tags).most_common(50)),
        }


def _format_validation_error(prefix: str, exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid value")
        msg = msg.removeprefix("Value error, ")
        out.append(f"{prefix}: {loc + ': ' if loc else ''}{msg}")
    return out


def _load_brand(folder: Path, root_real: Path, errors: list[str], warnings: list[str]) -> Brand | None:
    slug = folder.name
    label = f"{slug}/{BRAND_FILE}"
    brand_root = folder.resolve()
    try:
        brand_root.relative_to(root_real)
    except ValueError:
        errors.append(f"{slug}/: resolves outside the brands directory")
        return None
    brand_file = folder / BRAND_FILE
    if not brand_file.exists():
        hint = " (found brand.yml; rename it to brand.yaml)" if (folder / "brand.yml").exists() else ""
        errors.append(f"{label}: missing{hint}")
        return None
    try:
        real_file = brand_file.resolve(strict=True)
        real_file.relative_to(brand_root)
        if not real_file.is_file():
            raise ValueError
        if real_file.stat().st_size > MAX_BRAND_FILE_BYTES:
            errors.append(f"{label}: larger than {MAX_BRAND_FILE_BYTES // 1024} KiB")
            return None
        text = real_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"{label}: not valid UTF-8")
        return None
    except (OSError, ValueError):
        errors.append(f"{label}: must be a regular file inside the brand folder")
        return None
    try:
        raw = safe_load_yaml(text)
    except yaml.YAMLError as exc:
        errors.append(f"{label}: invalid YAML: {exc}")
        return None
    if not isinstance(raw, dict):
        errors.append(f"{label}: top level must be a mapping with at least a 'name'")
        return None
    try:
        spec = BrandSpec.model_validate(raw)
    except ValidationError as exc:
        errors.extend(_format_validation_error(label, exc))
        return None
    if spec.parent == slug:
        errors.append(f"{label}: parent: a brand cannot be its own parent")
        return None

    assets: dict[str, Asset] = {}
    for index, item in enumerate(spec.assets):
        where = f"{label}: assets[{index}] ({item.id})"
        candidate = folder / item.file
        try:
            real = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            errors.append(f"{where}: file not found: {item.file}")
            continue
        try:
            real.relative_to(brand_root)
        except ValueError:
            errors.append(f"{where}: {item.file} resolves outside the brand folder")
            continue
        if not real.is_file():
            errors.append(f"{where}: {item.file} is not a regular file")
            continue
        ext = real.suffix.lower()
        declared = EXTENSION_MIME.get(Path(item.file).suffix.lower())
        if declared is None or EXTENSION_MIME.get(ext) != declared:
            allowed = ", ".join(sorted(EXTENSION_MIME))
            errors.append(f"{where}: unsupported file type {Path(item.file).suffix or '(none)'} (allowed: {allowed})")
            continue
        size = real.stat().st_size
        if size > MAX_ASSET_BYTES:
            errors.append(f"{where}: larger than {MAX_ASSET_BYTES // (1024 * 1024)} MiB")
            continue
        data = real.read_bytes()
        sniffed = sniff_mime(data)
        if sniffed != declared:
            errors.append(f"{where}: content does not look like {declared} (detected {sniffed or 'unknown'})")
            continue
        dims = image_size(data, declared)
        active = declared == "image/svg+xml" and svg_has_active_content(data)
        if active:
            warnings.append(f"{where}: SVG contains scripts or event handlers")
        assets[item.id] = Asset(
            brand=slug,
            id=item.id,
            name=item.name,
            category=item.category,
            status=item.status,
            tags=tuple(item.tags),
            description=item.description,
            file=Path(item.file).as_posix(),
            size=size,
            sha256=hashlib.sha256(data).hexdigest(),
            mime=declared,
            width=dims[0] if dims else None,
            height=dims[1] if dims else None,
            active_content=active,
        )

    return Brand(
        slug=slug,
        name=spec.name,
        description=spec.description,
        parent=spec.parent,
        website=spec.website,
        root=brand_root,
        own_tokens=spec.flat_tokens,
        own_guidelines=list(spec.guidelines),
        assets=assets,
    )


def _resolve_inheritance(brands: dict[str, Brand], errors: list[str]) -> None:
    """Fill each brand's effective tokens and guidelines, walking up the ``parent`` chain."""
    for brand in brands.values():
        if brand.parent and brand.parent not in brands:
            errors.append(f"{brand.slug}/{BRAND_FILE}: parent: unknown brand {brand.parent!r}")
    if errors:
        return
    for brand in brands.values():
        chain: list[Brand] = []
        current: Brand | None = brand
        while current is not None:
            if current.slug in {b.slug for b in chain}:
                cycle = " -> ".join(b.slug for b in [*chain, current])
                errors.append(f"{brand.slug}/{BRAND_FILE}: parent: cycle detected ({cycle})")
                break
            if len(chain) > MAX_PARENT_DEPTH:
                errors.append(f"{brand.slug}/{BRAND_FILE}: parent chain deeper than {MAX_PARENT_DEPTH}")
                break
            chain.append(current)
            current = brands.get(current.parent) if current.parent else None
        else:
            tokens: dict[str, ResolvedToken] = {}
            guidelines: list[tuple[str, str]] = []
            for ancestor in reversed(chain):  # root first, the brand itself last (wins)
                for token in ancestor.own_tokens:
                    tokens[token.name] = ResolvedToken(token.name, token.value, token.description, ancestor.slug)
                guidelines.extend((rule, ancestor.slug) for rule in ancestor.own_guidelines)
            brand.tokens = list(tokens.values())
            brand.guidelines = guidelines
            if brand.parent:
                brands[brand.parent].children.append(brand.slug)
    for brand in brands.values():
        brand.children.sort()
