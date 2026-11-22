"""Export brand tokens as DTCG (Design Tokens Community Group) format, version 2025.10.

Rules:

* Only values that can be expressed in DTCG are exported. Anything else is listed in
  ``omitted`` with a machine-readable ``reason`` instead of being guessed.
* When a name is both a token and a group (``color.primary`` and ``color.primary.soft``), the
  token goes to the reserved ``$root`` key of the group, as DTCG 2025.10 specifies.
* ``space.*`` is accepted as an alias of ``spacing.*``.

Supported token families:

==========================  ==============  =========================================
Name prefix                 ``$type``       Accepted values
==========================  ==============  =========================================
``color.*``                 ``color``       ``#RGB``, ``#RRGGBB``, ``#RRGGBBAA``
``font.family.*``           ``fontFamily``  ``"Inter, sans-serif"`` or a list
``font.weight.*``           ``fontWeight``  1-1000 or a DTCG keyword (``bold``...)
``font.size.*``             ``dimension``   ``16``, ``"16px"``, ``"1.5rem"``
``font.line-height.*``      ``number``      ``1.5``
``spacing.*`` / ``space.*`` ``dimension``   ``8``, ``"8px"``, ``"0.5rem"``
``radius.*``                ``dimension``   ``12``, ``"12px"``
==========================  ==============  =========================================
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import __version__
from .catalog import Brand, Catalog
from .schema import HEX_RE

DTCG_SPEC = "https://www.designtokens.org/TR/2025.10/format/"
BRAND_JSON_VERSION = "1.0"

DIMENSION_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(px|rem)\s*$")
NUMBER_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")
UNSAFE_FONT_CHARS = re.compile(r"[;{}<>\\\"\n\r\t@]")

#: DTCG 2025.10 font weight keywords and their numeric equivalents.
FONT_WEIGHT_KEYWORDS = {
    "thin": 100,
    "hairline": 100,
    "extra-light": 200,
    "ultra-light": 200,
    "light": 300,
    "normal": 400,
    "regular": 400,
    "book": 400,
    "medium": 500,
    "semi-bold": 600,
    "demi-bold": 600,
    "bold": 700,
    "extra-bold": 800,
    "ultra-bold": 800,
    "black": 900,
    "heavy": 900,
    "extra-black": 950,
    "ultra-black": 950,
}

# Omission reasons.
UNSUPPORTED_TYPE = "unsupported_type"
UNCONVERTIBLE_VALUE = "unconvertible_value"
PATH_COLLISION = "path_collision"


# -- value converters --------------------------------------------------------------------------


def hex_to_dtcg(value: Any) -> dict[str, Any] | None:
    """``#RGB`` / ``#RRGGBB`` / ``#RRGGBBAA`` -> DTCG sRGB color value (components in 0..1)."""
    if not isinstance(value, str) or not HEX_RE.match(value.strip()):
        return None
    digits = value.strip()[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    channels = [int(digits[i : i + 2], 16) for i in range(0, len(digits), 2)]
    color: dict[str, Any] = {
        "colorSpace": "srgb",
        "components": [round(c / 255, 4) for c in channels[:3]],
        "hex": "#" + digits[:6].upper(),
    }
    if len(channels) == 4:
        color["alpha"] = round(channels[3] / 255, 4)
    return color


def font_family_to_dtcg(value: Any) -> str | list[str] | None:
    """``"Inter, system-ui, sans-serif"`` or ``["Inter", "sans-serif"]`` -> DTCG fontFamily."""
    if isinstance(value, list):
        names = [str(v).strip().strip("'\"").strip() for v in value]
    elif isinstance(value, str):
        names = [part.strip().strip("'\"").strip() for part in value.split(",")]
    else:
        return None
    names = [n for n in names if n]
    if not names or any(UNSAFE_FONT_CHARS.search(n) for n in names):
        return None
    return names[0] if len(names) == 1 else names


def font_weight_to_dtcg(value: Any) -> int | str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().lower()
        if text in FONT_WEIGHT_KEYWORDS:
            return text
        if not NUMBER_RE.match(text):
            return None
        number = float(text)
    else:
        return None
    if not number.is_integer() or not 1 <= number <= 1000:
        return None
    return int(number)


def dimension_to_dtcg(value: Any) -> dict[str, Any] | None:
    """``8`` -> 8px; ``"1.5rem"`` -> 1.5rem. DTCG 2025.10 only defines ``px`` and ``rem``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number, unit = float(value), "px"
    else:
        match = DIMENSION_RE.match(str(value))
        if not match:
            return None
        number, unit = float(match.group(1)), match.group(2)
    return {"value": int(number) if number.is_integer() else number, "unit": unit}


def number_to_dtcg(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and NUMBER_RE.match(value):
        number = float(value)
        return int(number) if number.is_integer() else number
    return None


# -- token classification ----------------------------------------------------------------------


@dataclass(frozen=True)
class DtcgToken:
    name: str  # original dotted name
    path: tuple[str, ...]  # path in the DTCG tree
    type: str
    value: Any
    description: str = ""

    def node(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"$type": self.type, "$value": self.value}
        if self.description:
            entry["$description"] = self.description
        return entry


def classify(name: str) -> tuple[str, list[str]] | None:
    """Return ``(dtcg_type, tree_path)`` for a token name, or None if the family is not exported."""
    parts = name.split(".")
    head, rest = parts[0], parts[1:]
    if head == "color":
        return "color", parts
    if head in ("spacing", "space"):
        return "dimension", ["spacing", *rest]
    if head == "radius":
        return "dimension", parts
    if head == "font" and rest:
        kind = rest[0]
        if kind == "family":
            return "fontFamily", parts
        if kind == "weight":
            return "fontWeight", parts
        if kind == "size":
            return "dimension", parts
        if kind in ("line-height", "lineHeight"):
            return "number", ["font", "line-height", *rest[1:]]
    return None


_CONVERTERS = {
    "color": hex_to_dtcg,
    "fontFamily": font_family_to_dtcg,
    "fontWeight": font_weight_to_dtcg,
    "dimension": dimension_to_dtcg,
    "number": number_to_dtcg,
}


def place(tree: dict[str, Any], path: Iterable[str], entry: dict[str, Any]) -> bool:
    """Insert a token in the group tree. Returns False on a collision.

    If a name is both a token and a group, the token moves to the group's ``$root``.
    """
    *groups, leaf = list(path)
    node = tree
    for part in groups:
        child = node.get(part)
        if child is None:
            child = node[part] = {}
        elif "$value" in child:  # an existing token becomes the $root of a new group
            child = node[part] = {"$root": child}
        node = child
    current = node.get(leaf)
    if current is None:
        node[leaf] = entry
        return True
    if "$value" not in current and "$root" not in current:  # existing group without a base token
        current["$root"] = entry
        return True
    return False


def convert_tokens(tokens: Iterable[Any]) -> tuple[dict[str, Any], list[DtcgToken], list[dict[str, str]]]:
    """Convert tokens (objects with ``name``, ``value`` and ``description``) to a DTCG tree.

    Returns ``(tree, exported, omitted)``.
    """
    tree: dict[str, Any] = {}
    exported: list[DtcgToken] = []
    omitted: list[dict[str, str]] = []
    for token in tokens:
        name = str(token.name)
        kind = classify(name)
        if kind is None:
            omitted.append(
                {"name": name, "reason": UNSUPPORTED_TYPE, "detail": "no DTCG mapping for this token family"}
            )
            continue
        dtcg_type, path = kind
        value = _CONVERTERS[dtcg_type](token.value)
        if value is None or len(path) < 2:
            omitted.append(
                {"name": name, "reason": UNCONVERTIBLE_VALUE, "detail": f"{token.value!r} is not a valid {dtcg_type}"}
            )
            continue
        item = DtcgToken(name, tuple(path), dtcg_type, value, getattr(token, "description", "") or "")
        if not place(tree, path, item.node()):
            omitted.append({"name": name, "reason": PATH_COLLISION, "detail": f"{'.'.join(path)} is already defined"})
            continue
        exported.append(item)
    return tree, exported, omitted


# -- brand.json --------------------------------------------------------------------------------


def export_tokens(brand: Brand) -> dict[str, Any]:
    """Pure DTCG token tree (a valid ``.tokens.json`` document)."""
    tree, _, _ = convert_tokens(brand.tokens)
    return tree


def export_brand_json(catalog: Catalog, slug: str) -> dict[str, Any]:
    """The brand as a self-contained ``brand.json``: DTCG tokens + logos + guidelines."""
    brand = catalog.get(slug)
    tree, _, omitted = convert_tokens(brand.tokens)
    logos = []
    for asset in brand.logos():
        logos.append(
            {
                "id": asset.id,
                "name": asset.name,
                "category": asset.category,
                "status": asset.status,
                "approved": asset.approved,
                "file": asset.file,
                "format": asset.format,
                "mime": asset.mime,
                "sha256": asset.sha256,
                "tags": list(asset.tags),
            }
        )
    return {
        "brand_json": BRAND_JSON_VERSION,
        "dtcg": DTCG_SPEC,
        "generator": f"brand-kit-mcp {__version__}",
        "brand": {
            "slug": brand.slug,
            "name": brand.name,
            "description": brand.description,
            "parent": brand.parent,
            "website": brand.website,
        },
        "tokens": tree,
        "logos": logos,
        "guidelines": [text for text, _source in brand.guidelines],
        "omitted": omitted,
    }
