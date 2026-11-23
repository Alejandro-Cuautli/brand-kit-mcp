from __future__ import annotations

import pytest

from brand_kit_mcp.dtcg import (
    DTCG_SPEC,
    PATH_COLLISION,
    UNCONVERTIBLE_VALUE,
    UNSUPPORTED_TYPE,
    convert_tokens,
    dimension_to_dtcg,
    export_brand_json,
    export_tokens,
    font_family_to_dtcg,
    font_weight_to_dtcg,
    hex_to_dtcg,
    place,
)
from brand_kit_mcp.schema import Token


def toks(**pairs) -> list[Token]:
    return [Token(name.replace("__", "."), value) for name, value in pairs.items()]


class TestHex:
    def test_six_digits(self):
        assert hex_to_dtcg("#0F4C5C") == {
            "colorSpace": "srgb",
            "components": [0.0588, 0.298, 0.3608],
            "hex": "#0F4C5C",
        }

    def test_three_digits_expand(self):
        value = hex_to_dtcg("#fa4")
        assert value["hex"] == "#FFAA44"
        assert value["components"] == [1.0, 0.6667, 0.2667]
        assert "alpha" not in value

    def test_eight_digits_alpha(self):
        value = hex_to_dtcg("#0F4C5CCC")
        assert value["hex"] == "#0F4C5C"
        assert value["alpha"] == 0.8

    def test_black_and_white(self):
        assert hex_to_dtcg("#000000")["components"] == [0.0, 0.0, 0.0]
        assert hex_to_dtcg("#FFFFFF")["components"] == [1.0, 1.0, 1.0]

    @pytest.mark.parametrize("bad", ["0F4C5C", "#12", "#1234", "#GGGGGG", "", None, 123, "#0F4C5C0"])
    def test_invalid(self, bad):
        assert hex_to_dtcg(bad) is None


class TestOtherValues:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (8, {"value": 8, "unit": "px"}),
            (2.5, {"value": 2.5, "unit": "px"}),
            ("16px", {"value": 16, "unit": "px"}),
            (" 1.5rem ", {"value": 1.5, "unit": "rem"}),
            ("-4px", {"value": -4, "unit": "px"}),
        ],
    )
    def test_dimension(self, raw, expected):
        assert dimension_to_dtcg(raw) == expected

    @pytest.mark.parametrize("bad", ["1em", "auto", "10 %", True, None, "px"])
    def test_dimension_invalid(self, bad):
        assert dimension_to_dtcg(bad) is None

    def test_font_family(self):
        assert font_family_to_dtcg("Inter") == "Inter"
        assert font_family_to_dtcg("'Fraunces', Georgia, serif") == ["Fraunces", "Georgia", "serif"]
        assert font_family_to_dtcg(["Inter", " sans-serif "]) == ["Inter", "sans-serif"]
        assert font_family_to_dtcg("Evil; } body { color: red") is None
        assert font_family_to_dtcg(" , ") is None

    def test_font_weight(self):
        assert font_weight_to_dtcg(700) == 700
        assert font_weight_to_dtcg("400") == 400
        assert font_weight_to_dtcg("Semi-Bold") == "semi-bold"
        assert font_weight_to_dtcg(0) is None
        assert font_weight_to_dtcg(1001) is None
        assert font_weight_to_dtcg("heavyish") is None


class TestTree:
    def test_root_when_token_comes_first(self):
        tree, exported, omitted = convert_tokens(toks(color__primary="#000", color__primary__soft="#111"))
        assert omitted == []
        assert len(exported) == 2
        primary = tree["color"]["primary"]
        assert primary["$root"]["$value"]["hex"] == "#000000"
        assert primary["soft"]["$value"]["hex"] == "#111111"
        assert "$value" not in primary

    def test_root_when_group_comes_first(self):
        tree, _, omitted = convert_tokens(toks(color__primary__soft="#111", color__primary="#000"))
        assert omitted == []
        assert tree["color"]["primary"]["$root"]["$value"]["hex"] == "#000000"

    def test_place_reports_collision(self):
        tree: dict = {}
        assert place(tree, ["a", "b"], {"$value": 1})
        assert not place(tree, ["a", "b"], {"$value": 2})

    def test_types(self):
        tree, _, omitted = convert_tokens(
            toks(
                font__family__body="Inter, sans-serif",
                font__weight__bold="bold",
                font__size__body="16px",
                radius__card=12,
            )
        )
        assert omitted == []
        assert tree["font"]["family"]["body"] == {"$type": "fontFamily", "$value": ["Inter", "sans-serif"]}
        assert tree["font"]["weight"]["bold"]["$type"] == "fontWeight"
        assert tree["font"]["size"]["body"]["$value"] == {"value": 16, "unit": "px"}
        assert tree["radius"]["card"]["$type"] == "dimension"

    def test_line_height_name(self):
        tree, _, omitted = convert_tokens([Token("font.line-height.body", 1.5)])
        assert omitted == []
        assert tree["font"]["line-height"]["body"] == {"$type": "number", "$value": 1.5}

    def test_spacing_px_rem_and_alias(self):
        tree, _, omitted = convert_tokens(toks(spacing__sm=8, spacing__md="16px", space__lg="1.5rem"))
        assert omitted == []
        assert tree["spacing"] == {
            "sm": {"$type": "dimension", "$value": {"value": 8, "unit": "px"}},
            "md": {"$type": "dimension", "$value": {"value": 16, "unit": "px"}},
            "lg": {"$type": "dimension", "$value": {"value": 1.5, "unit": "rem"}},
        }
        assert "space" not in tree

    def test_no_spacing_group_when_brand_has_none(self):
        tree, _, _ = convert_tokens(toks(color__a="#000"))
        assert set(tree) == {"color"}

    def test_omitted_reasons(self):
        _, exported, omitted = convert_tokens(
            toks(
                shadow__card="0 1px 2px #000",
                spacing__md="1em",
                color__bad="red",
                spacing__sm=8,
                space__sm=10,
            )
        )
        reasons = {o["name"]: o["reason"] for o in omitted}
        assert reasons == {
            "shadow.card": UNSUPPORTED_TYPE,
            "spacing.md": UNCONVERTIBLE_VALUE,
            "color.bad": UNCONVERTIBLE_VALUE,
            "space.sm": PATH_COLLISION,
        }
        assert [t.name for t in exported] == ["spacing.sm"]
        assert all(o["detail"] for o in omitted)

    def test_description(self):
        tree, _, _ = convert_tokens([Token("color.a", "#000", "Ink")])
        assert tree["color"]["a"]["$description"] == "Ink"


class TestBrandJson:
    def test_example_brand(self, catalog):
        doc = export_brand_json(catalog, "marlowtide")
        assert doc["dtcg"] == DTCG_SPEC
        assert doc["brand"]["slug"] == "marlowtide"
        assert doc["brand"]["parent"] is None
        tokens = doc["tokens"]
        assert tokens["color"]["primary"]["$root"]["$value"]["hex"] == "#0F4C5C"
        assert tokens["color"]["overlay"]["$value"]["alpha"] == 0.8
        assert tokens["color"]["focus"]["$value"]["hex"] == "#FFAA44"
        assert tokens["spacing"]["lg"]["$value"] == {"value": 1.5, "unit": "rem"}
        assert doc["omitted"] == [
            {
                "name": "font.style.tagline",
                "reason": UNSUPPORTED_TYPE,
                "detail": "no DTCG mapping for this token family",
            }
        ]
        ids = {logo["id"] for logo in doc["logos"]}
        assert "legacy-mark" not in ids  # quarantined
        assert "pattern-tide" not in ids  # not a logo category
        assert {"logo-horizontal", "logomark", "logomark-png", "icon-wave"} <= ids
        assert len(doc["guidelines"]) == 5

    def test_sub_brand_inherits_and_overrides(self, catalog):
        doc = export_brand_json(catalog, "marlowtide-trail")
        assert doc["brand"]["parent"] == "marlowtide"
        assert doc["tokens"]["color"]["accent"]["$value"]["hex"] == "#5B8C3A"
        assert doc["tokens"]["color"]["primary"]["$root"]["$value"]["hex"] == "#0F4C5C"
        assert doc["tokens"]["color"]["moss"]["deep"]["$value"]["hex"] == "#2F5233"
        assert len(doc["guidelines"]) == 6
        assert [logo["id"] for logo in doc["logos"]] == ["trail-mark"]

    def test_plain_tokens_tree(self, catalog):
        tree = export_tokens(catalog.get("marlowtide"))
        assert set(tree) == {"color", "font", "spacing", "radius"}
        assert "$schema" not in tree
