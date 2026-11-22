# Contributing

Thanks for your interest in brand-kit-mcp. Issues and pull requests are welcome, in English or
Spanish.

## Setup

```bash
git clone https://github.com/Alejandro-Cuautli/brand-kit-mcp.git
cd brand-kit-mcp
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check . && ruff format --check .
pytest
brand-kit-mcp validate examples/brands
```

- Keep the server read-only and free of outbound network calls. Write features belong behind an
  explicit opt-in and should be discussed in an issue first.
- Add tests for new behaviour. Tool changes should be covered through the in-memory MCP client
  (see `tests/test_server.py`).
- Update `CHANGELOG.md` under an "Unreleased" heading and both READMEs when user-facing behaviour
  changes.
- Example brands must stay fictional. Do not add real company names, logos or trademarks.

## Releasing (maintainers)

1. Bump `__version__` in `src/brand_kit_mcp/__init__.py` and move the changelog entries.
2. Tag `vX.Y.Z` and push the tag. The release workflow publishes to PyPI (Trusted Publishing) and
   pushes the Docker image to GitHub Container Registry.
