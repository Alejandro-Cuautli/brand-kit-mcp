from __future__ import annotations

import pytest

from brand_kit_mcp.css import export_css
from brand_kit_mcp.kit import contrast_ratio, generate_kit


class TestCss:
    def test_example(self, catalog):
        css = export_css(catalog.get("marlowtide"))
        assert css.splitlines()[1] == ":root {"
        assert "  --color-primary: #0F4C5C;" in css
        assert "  --color-primary-soft: #3E7C8C;" in css
        assert "  --color-overlay: #0F4C5CCC;" in css
        assert "  --color-focus: #FFAA44;" in css
        assert '  --font-family-body: "Inter", system-ui, sans-serif;' in css
        assert "  --font-weight-bold: 700;" in css
        assert "  --spacing-xs: 4px;" in css
        assert "  --spacing-lg: 1.5rem;" in css
        assert "/* Not exported: font.style.tagline (unsupported_type) */" in css

    def test_prefix(self, catalog):
        css = export_css(catalog.get("marlowtide"), prefix="tw")
        assert "--tw-color-accent: #F2A541;" in css

    @pytest.mark.parametrize("bad", ["1x", "UPPER", "a;b", "x" * 40, "a b"])
    def test_bad_prefix(self, catalog, bad):
        with pytest.raises(ValueError):
            export_css(catalog.get("marlowtide"), prefix=bad)

    def test_variable_collision_is_reported(self, make_brand):
        make_brand("acme", "name: Acme\ntokens:\n  color.a-b: '#000'\n  color.a.b: '#111'\n")
        css = export_css(make_brand.load().get("acme"))
        assert css.count("--color-a-b:") == 1
        assert "same variable as color.a-b" in css

    def test_inheritance(self, catalog):
        css = export_css(catalog.get("marlowtide-trail"))
        assert "--color-accent: #5B8C3A;" in css
        assert "--color-primary: #0F4C5C;" in css


class TestKit:
    def test_contrast(self):
        assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
        assert contrast_ratio("#777", "#777") == pytest.approx(1.0)

    def test_document(self, catalog):
        md = generate_kit(catalog.get("marlowtide"), "document")
        assert md.startswith("# Marlowtide brand kit (document)")
        assert "| `color.primary` | `#0F4C5C` |" in md
        assert "## Accessible color pairs (suggested)" in md
        assert "| `color.ink` | `color.paper` |" in md
        assert "**Horizontal logo** (`logo-horizontal`" in md
        assert "Retired mark" not in md  # quarantined
        assert "## Applying it to a document (suggested)" in md
        assert "Use deep tide teal" in md

    def test_presentation_and_parent(self, catalog):
        md = generate_kit(catalog.get("marlowtide-trail"), "presentation")
        assert "## Applying it to a presentation (suggested)" in md
        assert "Part of the **marlowtide** brand family" in md
        assert "*inherited*" in md

    def test_review_logo_listed_as_pending(self, make_brand):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'
        make_brand(
            "acme",
            "name: Acme\nassets:\n  - {id: a, file: a.svg, name: Draft logo, category: logo, status: review}\n",
            {"a.svg": svg},
        )
        md = generate_kit(make_brand.load().get("acme"))
        assert "No approved logos" in md
        assert "Pending review (do not use in final deliverables):\n- Draft logo (`a`)" in md

    def test_bad_kind(self, catalog):
        with pytest.raises(ValueError):
            generate_kit(catalog.get("marlowtide"), "poster")  # type: ignore[arg-type]
