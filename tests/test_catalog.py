from __future__ import annotations

import os

import pytest

from brand_kit_mcp.catalog import MAX_BRAND_FILE_BYTES, Catalog, CatalogError, NotFound
from brand_kit_mcp.schema import flatten_tokens

from .conftest import SVG, PNG_1x1

MINIMAL = """
name: Acme
tokens:
  color:
    ink: "#111111"
"""


def errors_of(factory) -> str:
    with pytest.raises(CatalogError) as info:
        factory.load()
    return "\n".join(info.value.errors)


class TestExamples:
    def test_loads(self, catalog):
        assert catalog.slugs == ["tidewren", "tidewren-trail"]
        brand = catalog.get("tidewren")
        assert brand.children == ["tidewren-trail"]
        assert catalog.warnings == []

    def test_lookup_by_name(self, catalog):
        assert catalog.get("Tidewren Trail").slug == "tidewren-trail"
        with pytest.raises(NotFound, match="available brands: tidewren, tidewren-trail"):
            catalog.get("nope")

    def test_quarantined_is_hidden(self, catalog):
        visible = {a.id for a in catalog.assets("tidewren")}
        assert "legacy-mark" not in visible
        assert "pattern-tide" in visible
        with pytest.raises(NotFound):
            catalog.get_asset("tidewren", "legacy-mark")

    def test_asset_metadata(self, catalog):
        png = catalog.get_asset("tidewren", "logomark-png")
        assert (png.mime, png.width, png.height) == ("image/png", 256, 256)
        assert len(png.sha256) == 64
        svg = catalog.get_asset("tidewren", "logo-horizontal")
        assert (svg.width, svg.height) == (280, 64)
        assert "notice" not in svg.summary()
        review = catalog.get_asset("tidewren", "pattern-tide")
        assert review.summary()["approved"] is False
        assert "notice" in review.summary()

    def test_summary_has_no_absolute_paths(self, catalog):
        for asset in catalog.assets():
            assert not os.path.isabs(asset.summary()["file"])
            assert str(catalog.root) not in str(asset.summary())


class TestTokensSchema:
    def test_nested_flat_and_root(self):
        tokens = flatten_tokens(
            {
                "color": {
                    "primary": {"$root": "#000", "soft": "#111"},
                    "accent": {"$value": "#222", "$description": "x"},
                },
                "spacing.sm": 8,
            }
        )
        assert [(t.name, t.value) for t in tokens] == [
            ("color.primary", "#000"),
            ("color.primary.soft", "#111"),
            ("color.accent", "#222"),
            ("spacing.sm", 8),
        ]
        assert tokens[2].description == "x"

    @pytest.mark.parametrize(
        ("tree", "message"),
        [
            ({"color": {"ink": None}}, "quote hex colors"),
            ({"color": {"ink": "blue"}}, "color tokens must be hex"),
            ({"color": {"ink": True}}, "booleans"),
            ({"ink": "#000"}, "needs a group"),
            ({"color.ink": "#000", "color": {"ink": "#111"}}, "defined more than once"),
            ({"color": {"bad name": "#000"}}, "invalid name segment"),
            ({"color": {"$type": "color"}}, "unknown key"),
            ({"color": {}}, "empty group"),
        ],
    )
    def test_errors(self, tree, message):
        with pytest.raises(ValueError, match=message):
            flatten_tokens(tree)


class TestValidation:
    def test_minimal_brand(self, make_brand):
        make_brand("acme", MINIMAL)
        catalog = make_brand.load()
        assert catalog.get("acme").tokens[0].name == "color.ink"

    def test_unquoted_hex_gets_a_hint(self, make_brand):
        make_brand("acme", "name: Acme\ntokens:\n  color:\n    ink: #111111\n")
        assert "quote hex colors" in errors_of(make_brand)

    def test_unknown_field(self, make_brand):
        make_brand("acme", "name: Acme\ncolour: red\n")
        assert "acme/brand.yaml: colour: Extra inputs are not permitted" in errors_of(make_brand)

    def test_invalid_folder_slug(self, make_brand):
        make_brand("Bad_Slug", MINIMAL)
        assert "not a valid slug" in errors_of(make_brand)

    def test_hidden_and_underscore_folders_are_ignored(self, make_brand):
        make_brand("acme", MINIMAL)
        make_brand("_drafts", "not: [valid")
        (make_brand.root / ".git").mkdir()
        assert make_brand.load().slugs == ["acme"]

    def test_missing_brand_file(self, make_brand):
        (make_brand.root / "acme").mkdir()
        (make_brand.root / "acme" / "brand.yml").write_text(MINIMAL)
        assert "rename it to brand.yaml" in errors_of(make_brand)

    def test_unknown_parent_and_cycle(self, make_brand):
        make_brand("a", "name: A\nparent: b\n")
        make_brand("b", "name: B\nparent: a\n")
        make_brand("c", "name: C\nparent: zzz\n")
        assert "unknown brand 'zzz'" in errors_of(make_brand)
        (make_brand.root / "c" / "brand.yaml").write_text("name: C\n")
        assert "cycle detected" in errors_of(make_brand)

    def test_yaml_aliases_rejected(self, make_brand):
        make_brand("acme", "name: &n Acme\ndescription: *n\n")
        assert "aliases are not allowed" in errors_of(make_brand)

    def test_python_tags_rejected(self, make_brand):
        make_brand("acme", "name: !!python/object/apply:os.system ['echo hi']\n")
        assert "invalid YAML" in errors_of(make_brand)

    def test_brand_file_size_limit(self, make_brand):
        make_brand("acme", "name: Acme\ndescription: '" + "x" * MAX_BRAND_FILE_BYTES + "'\n")
        assert "larger than" in errors_of(make_brand)

    def test_duplicate_asset_ids(self, make_brand):
        make_brand(
            "acme",
            """
            name: Acme
            assets:
              - {id: a, file: a.svg, name: A, category: logo}
              - {id: a, file: a.svg, name: B, category: logo}
            """,
            {"a.svg": SVG},
        )
        assert "duplicate asset id 'a'" in errors_of(make_brand)

    def test_content_must_match_extension(self, make_brand):
        make_brand(
            "acme",
            "name: Acme\nassets:\n  - {id: a, file: a.png, name: A, category: logo}\n",
            {"a.png": SVG},
        )
        assert "does not look like image/png" in errors_of(make_brand)

    def test_unsupported_extension(self, make_brand):
        make_brand(
            "acme",
            "name: Acme\nassets:\n  - {id: a, file: run.sh, name: A, category: other}\n",
            {"run.sh": b"#!/bin/sh\n"},
        )
        assert "unsupported file type .sh" in errors_of(make_brand)

    def test_default_status_is_review(self, make_brand):
        make_brand("acme", "name: Acme\nassets:\n  - {id: a, file: a.svg, name: A, category: logo}\n", {"a.svg": SVG})
        assert make_brand.load().get_asset("acme", "a").approved is False

    def test_svg_with_script_warns(self, make_brand):
        evil = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        make_brand("acme", "name: Acme\nassets:\n  - {id: a, file: a.svg, name: A, category: logo}\n", {"a.svg": evil})
        catalog = make_brand.load()
        assert any("scripts" in w for w in catalog.warnings)
        assert "warning" in catalog.get_asset("acme", "a").summary()


class TestPathSafety:
    @pytest.mark.parametrize(
        "bad", ["../outside.svg", "assets/../../outside.svg", "/etc/passwd", "C:/x.svg", "a\\b.svg"]
    )
    def test_traversal_rejected_by_schema(self, make_brand, bad):
        make_brand("acme", f"name: Acme\nassets:\n  - {{id: a, file: '{bad}', name: A, category: logo}}\n")
        assert "assets.0.file" in errors_of(make_brand)

    def test_symlink_pointing_outside_is_rejected(self, make_brand, tmp_path):
        secret = tmp_path / "secret.svg"
        secret.write_bytes(SVG)
        folder = make_brand("acme", "name: Acme\nassets:\n  - {id: a, file: assets/a.svg, name: A, category: logo}\n")
        (folder / "assets").mkdir()
        (folder / "assets" / "a.svg").symlink_to(secret)
        assert "resolves outside the brand folder" in errors_of(make_brand)

    def test_symlinked_directory_pointing_outside_is_rejected(self, make_brand, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "a.svg").write_bytes(SVG)
        folder = make_brand("acme", "name: Acme\nassets:\n  - {id: a, file: assets/a.svg, name: A, category: logo}\n")
        (folder / "assets").symlink_to(outside, target_is_directory=True)
        assert "resolves outside the brand folder" in errors_of(make_brand)

    def test_symlink_inside_the_brand_is_fine(self, make_brand):
        folder = make_brand(
            "acme",
            "name: Acme\nassets:\n  - {id: a, file: link.png, name: A, category: logo}\n",
            {"real/a.png": PNG_1x1},
        )
        (folder / "link.png").symlink_to(folder / "real" / "a.png")
        assert make_brand.load().get_asset("acme", "a").width == 1

    def test_symlinked_brand_folder_rejected(self, make_brand, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "brand.yaml").write_text(MINIMAL)
        (make_brand.root / "acme").symlink_to(elsewhere, target_is_directory=True)
        assert "symlinked brand folders are not allowed" in errors_of(make_brand)

    def test_read_rechecks_the_path(self, make_brand, tmp_path):
        folder = make_brand(
            "acme",
            "name: Acme\nassets:\n  - {id: a, file: a.png, name: A, category: logo, status: approved}\n",
            {"a.png": PNG_1x1},
        )
        catalog = make_brand.load()
        asset = catalog.get_asset("acme", "a")
        assert catalog.read_asset(asset, 1024) == PNG_1x1
        assert catalog.read_asset(asset, 10) is None  # over the limit
        # swap the file for a symlink to something outside after startup
        secret = tmp_path / "secret.png"
        secret.write_bytes(PNG_1x1)
        (folder / "a.png").unlink()
        (folder / "a.png").symlink_to(secret)
        assert catalog.read_asset(asset, 1024) is None


def test_missing_directory(tmp_path):
    with pytest.raises(CatalogError, match="brands directory not found"):
        Catalog.load(tmp_path / "nope")


def test_empty_directory_warns(tmp_path):
    catalog = Catalog.load(tmp_path)
    assert len(catalog) == 0
    assert "no brands found" in catalog.warnings[0]
