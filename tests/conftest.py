from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from brand_kit_mcp.catalog import Catalog

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "brands"

PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005000201a3e2b8b10000000049454e44ae426082"
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="20" viewBox="0 0 10 20"><rect/></svg>'


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture
def catalog() -> Catalog:
    return Catalog.load(EXAMPLES)


class BrandFactory:
    """Write throwaway brands into a temporary brands directory."""

    def __init__(self, root: Path):
        self.root = root

    def __call__(self, slug: str, yaml_text: str, files: dict[str, bytes] | None = None) -> Path:
        folder = self.root / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "brand.yaml").write_text(textwrap.dedent(yaml_text), encoding="utf-8")
        for rel, data in (files or {}).items():
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return folder

    def load(self) -> Catalog:
        return Catalog.load(self.root)


@pytest.fixture
def make_brand(tmp_path: Path) -> BrandFactory:
    root = tmp_path / "brands"
    root.mkdir()
    return BrandFactory(root)
