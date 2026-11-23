# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-23

First public release.

### Added

- Brands as files: `brands/<slug>/brand.yaml` plus assets, validated with clear errors at startup.
- Sub-brands through `parent`: tokens and guidelines are inherited and can be overridden.
- Read-only MCP tools: `list_brands`, `get_brand`, `get_palette`, `get_typography`, `get_logos`,
  `search_assets`, `get_asset`, `export_brand_json`, `export_tokens_css`, `generate_brand_kit`.
- Resources `brand://{slug}/brand.json`, `brand://{slug}/tokens.css` and `brand://{slug}/kit.md`.
- DTCG 2025.10 export (colors, font families, weights, sizes, line heights, spacing, radius) with
  `$root` for names that are both a token and a group, and an `omitted` list with reasons.
- CSS custom properties export.
- Asset review states: `approved`, `review` (flagged) and `quarantined` (never served).
- PNG/JPEG/WebP up to 2 MB embedded as MCP images; SVG returned as text.
- stdio and streamable HTTP transports; optional Bearer token for HTTP (`BRAND_KIT_TOKEN`).
- CLI: `brand-kit-mcp validate` and `brand-kit-mcp export --format dtcg|brand-json|css`.
- `--demo` flag with the fictional *Marlowtide* example brands.
- Docker image (HTTP mode, non-root).

[0.1.0]: https://github.com/Alejandro-Cuautli/brand-kit-mcp/releases/tag/v0.1.0
