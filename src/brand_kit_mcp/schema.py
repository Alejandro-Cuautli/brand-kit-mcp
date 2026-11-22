"""The `brand.yaml` schema.

A brand is a folder: ``<brands-dir>/<slug>/brand.yaml`` plus the asset files it references.
This module only validates the *shape* of the file. File-system checks (asset paths, sizes,
symlinks) live in :mod:`brand_kit_mcp.catalog`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
ASSET_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9 _.-]{0,39}$")
TOKEN_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

MAX_TOKENS = 5000
MAX_TOKEN_DEPTH = 10
MAX_TOKEN_TEXT = 1000

Category = Literal["logo", "logomark", "wordmark", "icon", "illustration", "pattern", "photo", "template", "other"]
Status = Literal["approved", "review", "quarantined"]

#: Categories that `get_logos` and the brand.json export treat as logos.
LOGO_CATEGORIES = frozenset({"logo", "logomark", "wordmark", "icon"})

TokenValue = Union[str, int, float, list[str]]


@dataclass(frozen=True)
class Token:
    """A design token after flattening: ``color.primary.soft`` -> value."""

    name: str
    value: TokenValue
    description: str = ""

    @property
    def path(self) -> list[str]:
        return self.name.split(".")


class AssetSpec(BaseModel):
    """One entry of the ``assets`` list."""

    model_config = ConfigDict(extra="forbid")

    id: str
    file: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=200)
    category: Category
    status: Status = "review"
    tags: list[str] = Field(default_factory=list, max_length=32)
    description: str = Field(default="", max_length=2000)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not ASSET_ID_RE.match(value):
            raise ValueError(
                "asset id must be 1-64 chars of lowercase letters, digits, '.', '_' or '-' "
                f"and start/end with a letter or digit (got {value!r})"
            )
        return value

    @field_validator("file")
    @classmethod
    def _check_file(cls, value: str) -> str:
        if "\x00" in value or "\\" in value:
            raise ValueError("asset file must be a plain relative path with '/' separators")
        if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
            raise ValueError("asset file must be relative to the brand folder, not absolute")
        if ".." in value.split("/"):
            raise ValueError("asset file must not contain '..'")
        return value

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for tag in value:
            tag = str(tag).strip().lower()
            if not TAG_RE.match(tag):
                raise ValueError(f"invalid tag {tag!r}: use up to 40 lowercase letters, digits, spaces, '.', '_', '-'")
            if tag not in cleaned:
                cleaned.append(tag)
        return cleaned


class BrandSpec(BaseModel):
    """The whole ``brand.yaml`` document."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    parent: str | None = None
    website: str | None = Field(default=None, max_length=300)
    tokens: dict[str, Any] = Field(default_factory=dict)
    guidelines: list[str] = Field(default_factory=list, max_length=200)
    assets: list[AssetSpec] = Field(default_factory=list, max_length=5000)

    _flat_tokens: list[Token] = PrivateAttr(default_factory=list)

    @field_validator("parent")
    @classmethod
    def _check_parent(cls, value: str | None) -> str | None:
        if value is not None and not SLUG_RE.match(value):
            raise ValueError(f"parent must be a brand slug (got {value!r})")
        return value

    @field_validator("website")
    @classmethod
    def _check_website(cls, value: str | None) -> str | None:
        if value is not None and not re.match(r"^https?://[^\s]+$", value):
            raise ValueError("website must be an http(s) URL")
        return value

    @field_validator("guidelines")
    @classmethod
    def _check_guidelines(cls, value: list[str]) -> list[str]:
        out = []
        for i, rule in enumerate(value):
            rule = str(rule).strip()
            if not rule:
                raise ValueError(f"guideline #{i + 1} is empty")
            if len(rule) > 2000:
                raise ValueError(f"guideline #{i + 1} is longer than 2000 characters")
            out.append(rule)
        return out

    @model_validator(mode="after")
    def _flatten(self) -> BrandSpec:
        self._flat_tokens = flatten_tokens(self.tokens)
        seen: set[str] = set()
        for asset in self.assets:
            if asset.id in seen:
                raise ValueError(f"duplicate asset id {asset.id!r}")
            seen.add(asset.id)
        return self

    @property
    def flat_tokens(self) -> list[Token]:
        return list(self._flat_tokens)


def flatten_tokens(tree: dict[str, Any]) -> list[Token]:
    """Flatten the nested ``tokens`` mapping into dotted names.

    Accepted shapes (they can be mixed)::

        color.primary: "#0F4C5C"          # dotted key
        color:
          primary:
            $root: "#0F4C5C"              # value of the group itself
            soft: "#3E7C8C"
          accent:
            $value: "#F2A541"             # explicit token with a description
            $description: Call-to-action color

    Raises ``ValueError`` listing every problem found.
    """
    errors: list[str] = []
    tokens: dict[str, Token] = {}

    def add(path: list[str], raw: Any) -> None:
        name = ".".join(path)
        description = ""
        if isinstance(raw, dict):
            extra = set(raw) - {"$value", "$description"}
            if extra:
                errors.append(f"{name}: unexpected keys {sorted(extra)} next to $value")
                return
            description = str(raw.get("$description") or "").strip()
            raw = raw.get("$value")
        problem = _check_value(name, raw)
        if problem:
            errors.append(f"{name}: {problem}")
            return
        if len(path) < 2:
            errors.append(f"{name}: a token needs a group and a name, e.g. 'color.{name}'")
            return
        if name in tokens:
            errors.append(f"{name}: defined more than once")
            return
        if len(tokens) >= MAX_TOKENS:
            errors.append(f"more than {MAX_TOKENS} tokens")
            return
        value = [str(v).strip() for v in raw] if isinstance(raw, list) else raw
        tokens[name] = Token(name=name, value=value, description=description)

    def walk(node: dict[str, Any], prefix: list[str]) -> None:
        if len(prefix) > MAX_TOKEN_DEPTH:
            errors.append(f"{'.'.join(prefix)}: nesting deeper than {MAX_TOKEN_DEPTH} levels")
            return
        for key, value in node.items():
            where = ".".join([*prefix, str(key)])
            if not isinstance(key, str):
                errors.append(f"{where}: token keys must be strings")
                continue
            if key == "$root":
                if not prefix:
                    errors.append("$root is only allowed inside a group")
                    continue
                if isinstance(value, dict) and "$value" not in value:
                    errors.append(f"{where}: $root must hold a value, not a group")
                    continue
                add(prefix, value)
                continue
            if key.startswith("$"):
                errors.append(f"{where}: unknown key {key!r} (only $root, $value and $description are allowed)")
                continue
            parts = key.split(".")
            bad = [p for p in parts if not TOKEN_PART_RE.match(p)]
            if bad:
                errors.append(f"{where}: invalid name segment {bad[0]!r} (letters, digits, '_' and '-' only)")
                continue
            path = [*prefix, *parts]
            if isinstance(value, dict) and "$value" not in value:
                if not value:
                    errors.append(f"{where}: empty group")
                    continue
                walk(value, path)
            else:
                add(path, value)

    if not isinstance(tree, dict):
        raise ValueError("tokens must be a mapping")
    walk(tree, [])
    if errors:
        raise ValueError("invalid tokens:\n  - " + "\n  - ".join(errors))
    return list(tokens.values())


def _check_value(name: str, raw: Any) -> str | None:
    if raw is None:
        hint = " (hint: quote hex colors; an unquoted '#' starts a YAML comment)" if name.startswith("color.") else ""
        return "missing value" + hint
    if isinstance(raw, bool):
        return "booleans are not valid token values"
    if isinstance(raw, list):
        if not raw or not all(isinstance(v, str) and v.strip() for v in raw):
            return "list values must be non-empty lists of strings (used for font stacks)"
        if any(len(v) > 200 for v in raw) or len(raw) > 20:
            return "list value too long"
    elif isinstance(raw, str):
        if len(raw) > MAX_TOKEN_TEXT:
            return f"value longer than {MAX_TOKEN_TEXT} characters"
        if not raw.strip():
            return "empty value"
    elif not isinstance(raw, (int, float)):
        return f"unsupported value type {type(raw).__name__}"
    if name.startswith("color.") and not (isinstance(raw, str) and HEX_RE.match(raw.strip())):
        return f"color tokens must be hex strings like '#RGB', '#RRGGBB' or '#RRGGBBAA' (got {raw!r})"
    return None
