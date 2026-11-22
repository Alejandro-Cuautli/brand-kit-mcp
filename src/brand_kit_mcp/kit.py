"""A markdown brand kit an agent can follow while writing a document or building a deck.

The kit only states what the brand files declare. Suggestions (which colors pair well, which
font goes where) are derived mechanically from those values, e.g. WCAG contrast ratios, and are
labelled as suggestions.
"""

from __future__ import annotations

from itertools import permutations
from typing import Literal

from .catalog import Brand, ResolvedToken
from .css import css_value
from .dtcg import convert_tokens

KitKind = Literal["document", "presentation"]


def relative_luminance(hex_value: str) -> float:
    digits = hex_value.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)

    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def _opaque_colors(brand: Brand) -> list[ResolvedToken]:
    out = []
    for token in brand.tokens_with_prefix("color."):
        digits = str(token.value).strip().lstrip("#")
        if len(digits) == 8 and digits[6:].lower() != "ff":
            continue  # translucent colors depend on what is behind them
        out.append(token)
    return out


def _wcag(ratio: float) -> str:
    if ratio >= 7:
        return "AAA"
    if ratio >= 4.5:
        return "AA"
    if ratio >= 3:
        return "AA large text"
    return "fail"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _pick(tokens: list[ResolvedToken], *names: str) -> ResolvedToken | None:
    by_name = {t.name: t for t in tokens}
    for name in names:
        if name in by_name:
            return by_name[name]
    return tokens[0] if tokens else None


def generate_kit(brand: Brand, kind: KitKind = "document") -> str:
    if kind not in ("document", "presentation"):
        raise ValueError("kind must be 'document' or 'presentation'")
    colors = brand.tokens_with_prefix("color.")
    families = brand.tokens_with_prefix("font.family.", "font.family")
    _, exported, _ = convert_tokens(brand.tokens)
    css = {t.name: css_value(t) for t in exported}

    lines = [f"# {brand.name} brand kit ({kind})", ""]
    if brand.description:
        lines += [brand.description, ""]
    lines += [
        "> Use only the colors, fonts and logos listed here. Do not invent new brand colors, "
        "redraw logos or use assets marked as pending review.",
        "",
    ]
    if brand.parent:
        lines += [f"Part of the **{brand.parent}** brand family; values marked *inherited* come from it.", ""]

    lines += ["## Palette", ""]
    if colors:
        lines += ["| Token | Hex | Notes |", "| --- | --- | --- |"]
        for t in colors:
            note = t.description or ""
            if t.source != brand.slug:
                note = (note + " " if note else "") + "*inherited*"
            lines.append(f"| `{t.name}` | `{_cell(t.value)}` | {_cell(note)} |")
    else:
        lines.append("No colors declared.")
    lines.append("")

    lines += ["## Typography", ""]
    fonts = brand.tokens_with_prefix("font.")
    if fonts:
        lines += ["| Token | Value | Notes |", "| --- | --- | --- |"]
        for t in fonts:
            value = css.get(t.name, t.value if not isinstance(t.value, list) else ", ".join(t.value))
            lines.append(f"| `{t.name}` | `{_cell(value)}` | {_cell(t.description)} |")
    else:
        lines.append("No typography declared.")
    lines.append("")

    spacing = brand.tokens_with_prefix("spacing.", "space.", "radius.")
    if spacing:
        lines += ["## Spacing and radius", ""]
        lines += [f"- `{t.name}`: {css.get(t.name, t.value)}" for t in spacing]
        lines.append("")

    pairs = []
    opaque = _opaque_colors(brand)
    for fg, bg in permutations(opaque, 2):
        ratio = contrast_ratio(str(fg.value), str(bg.value))
        if ratio >= 3:
            pairs.append((ratio, fg, bg))
    if pairs:
        pairs.sort(key=lambda p: (-p[0], p[1].name, p[2].name))
        seen: set[frozenset[str]] = set()
        lines += ["## Accessible color pairs (suggested)", ""]
        lines += ["| Text | Background | Contrast | WCAG 2.x |", "| --- | --- | --- | --- |"]
        for ratio, fg, bg in pairs:
            key = frozenset((fg.name, bg.name))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"| `{fg.name}` | `{bg.name}` | {ratio:.2f}:1 | {_wcag(ratio)} |")
            if len(seen) >= 8:
                break
        lines.append("")

    logos = brand.logos()
    approved = [a for a in logos if a.approved]
    pending = [a for a in logos if not a.approved]
    lines += ["## Logos and icons", ""]
    if approved:
        for a in approved:
            dims = f", {a.width}x{a.height}" if a.width and a.height else ""
            lines.append(f"- **{a.name}** (`{a.id}`, {a.format}{dims}): fetch with `get_asset`.")
    else:
        lines.append("- No approved logos. Do not improvise one; use the brand name in the heading font.")
    if pending:
        lines += ["", "Pending review (do not use in final deliverables):"]
        lines += [f"- {a.name} (`{a.id}`)" for a in pending]
    lines.append("")

    if brand.guidelines:
        lines += ["## Guidelines", ""]
        lines += [f"{i}. {text}" for i, (text, _src) in enumerate(brand.guidelines, start=1)]
        lines.append("")

    lines += _how_to(brand, kind, opaque, families)
    return "\n".join(lines).rstrip() + "\n"


def _how_to(brand: Brand, kind: KitKind, opaque: list[ResolvedToken], families: list[ResolvedToken]) -> list[str]:
    heading = _pick(families, "font.family.heading", "font.family.display", "font.family.title")
    body = _pick(families, "font.family.body", "font.family.text", "font.family.base")
    by_lum = sorted(opaque, key=lambda t: relative_luminance(str(t.value)))
    darkest = by_lum[0] if by_lum else None
    lightest = by_lum[-1] if by_lum else None
    accent = _pick(opaque, "color.accent", "color.secondary", "color.primary")
    primary = _pick(opaque, "color.primary", "color.brand")

    title = "Applying it to a document" if kind == "document" else "Applying it to a presentation"
    out = [f"## {title} (suggested)", ""]
    if heading:
        out.append(f"- Headings: `{heading.name}`.")
    if body:
        out.append(f"- Body text: `{body.name}`.")
    if kind == "document":
        if darkest and lightest and darkest.name != lightest.name:
            out.append(f"- Text `{darkest.name}` on page background `{lightest.name}`.")
        if accent:
            out.append(f"- Use `{accent.name}` sparingly: links, callouts and key figures.")
        out.append("- Place the primary logo once, in the header or on the cover; keep clear space around it.")
    else:
        if primary and lightest and primary.name != lightest.name:
            ratio = contrast_ratio(str(lightest.value), str(primary.value))
            note = "" if ratio >= 3 else " (low contrast: use it for shapes, not text)"
            out.append(f"- Title and section slides: `{primary.name}` background with `{lightest.name}` text{note}.")
        if darkest and lightest and darkest.name != lightest.name:
            out.append(f"- Content slides: `{lightest.name}` background with `{darkest.name}` text.")
        if accent:
            out.append(f"- Highlight one idea per slide with `{accent.name}`.")
        out.append("- Logo on the first and last slide only; prefer the logomark on small footers.")
    out.append("- Check every guideline above before delivering.")
    return out
