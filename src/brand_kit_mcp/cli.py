"""Command line entry point: ``brand-kit-mcp [serve|validate|export]``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .catalog import Catalog, CatalogError, NotFound
from .dtcg import convert_tokens, export_brand_json, export_tokens

ENV_DIR = "BRAND_KIT_DIR"
ENV_TOKEN = "BRAND_KIT_TOKEN"  # noqa: S105 - variable name, not a secret
ENV_ALLOWED_HOSTS = "BRAND_KIT_ALLOWED_HOSTS"
COMMANDS = ("serve", "validate", "export")


def demo_brands_dir() -> Path:
    """The example brands shipped with the package (or the repository checkout)."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "examples" / "brands", here.parents[1] / "examples" / "brands"):
        if candidate.is_dir():
            return candidate
    raise CatalogError(["example brands are not available in this installation"])


def _default_dir() -> str:
    return os.environ.get(ENV_DIR) or "./brands"


def _load(path: str | Path) -> Catalog:
    if not Path(path).expanduser().exists():
        raise CatalogError(
            [
                f"brands directory not found: {path}"
                f" (pass --brands-dir /absolute/path/to/brands, set {ENV_DIR}, or try the examples with --demo)"
            ]
        )
    catalog = Catalog.load(path)
    for warning in catalog.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return catalog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brand-kit-mcp",
        description="MCP server that gives AI agents your brand identity and exports it as DTCG tokens.",
    )
    parser.add_argument("--version", action="version", version=f"brand-kit-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the MCP server (default)")
    serve.add_argument("--brands-dir", default=None, help=f"brands directory (env {ENV_DIR}, default ./brands)")
    serve.add_argument("--demo", action="store_true", help="serve the bundled example brands")
    serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    serve.add_argument(
        "--log-level", default="warning", choices=["debug", "info", "warning", "error"], help="HTTP log level"
    )

    validate = sub.add_parser("validate", help="check a brands directory and report problems")
    validate.add_argument("dir", nargs="?", default=None, help=f"brands directory (env {ENV_DIR}, default ./brands)")

    export = sub.add_parser("export", help="print a brand's tokens to stdout")
    export.add_argument("slug")
    export.add_argument("--brands-dir", default=None)
    export.add_argument(
        "--format",
        choices=["dtcg", "brand-json", "css"],
        default="dtcg",
        help="dtcg: plain DTCG token tree; brand-json: tokens + logos + guidelines; css: custom properties",
    )
    export.add_argument("--prefix", default="", help="CSS variable prefix")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or (args_list[0] not in COMMANDS and args_list[0] not in ("-h", "--help", "--version")):
        args_list.insert(0, "serve")
    args = _parser().parse_args(args_list)
    try:
        if args.command == "validate":
            return _validate(args.dir or _default_dir())
        if args.command == "export":
            return _export(args)
        return _serve(args)
    except CatalogError as exc:
        print("brand-kit-mcp: the brands directory is not valid:", file=sys.stderr)
        for error in exc.errors:
            detail = error.replace("\n", "\n         ")  # keep multi-line errors under their "error:"
            print(f"  error: {detail}", file=sys.stderr)
        return 2
    except NotFound as exc:
        print(f"brand-kit-mcp: {exc}", file=sys.stderr)
        return 2


def _serve(args: argparse.Namespace) -> int:
    from .server import build_server

    directory = demo_brands_dir() if args.demo else (args.brands_dir or _default_dir())
    catalog = _load(directory)
    server = build_server(catalog)
    if args.transport == "stdio":
        server.run("stdio")
        return 0
    from .http_transport import run_http

    token = os.environ.get(ENV_TOKEN) or None
    hosts = [h.strip() for h in os.environ.get(ENV_ALLOWED_HOSTS, "").split(",") if h.strip()] or None
    run_http(server, host=args.host, port=args.port, token=token, allowed_hosts=hosts, log_level=args.log_level)
    return 0


def _validate(directory: str) -> int:
    catalog = _load(directory)
    tokens = assets = 0
    for brand in catalog:
        _, _, omitted = convert_tokens(brand.tokens)
        visible = brand.visible_assets()
        tokens += len(brand.tokens)
        assets += len(visible)
        parent = f" (parent: {brand.parent})" if brand.parent else ""
        review = sum(1 for a in visible if not a.approved)
        hidden = len(brand.assets) - len(visible)
        print(
            f"ok  {brand.slug}{parent}: {_plural(len(brand.tokens), 'token')}, {_plural(len(visible), 'asset')}"
            f" ({review} pending review, {hidden} quarantined)"
        )
        for item in omitted:
            print(f"    note: {item['name']} is not exported to DTCG ({item['reason']}: {item['detail']})")
    print(f"{_plural(len(catalog), 'brand')}, {_plural(tokens, 'token')}, {_plural(assets, 'usable asset')}")
    return 0


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _export(args: argparse.Namespace) -> int:
    catalog = _load(args.brands_dir or _default_dir())
    brand = catalog.get(args.slug)
    if args.format == "css":
        from .css import export_css

        try:
            sys.stdout.write(export_css(brand, args.prefix))
        except ValueError as exc:
            print(f"brand-kit-mcp: {exc}", file=sys.stderr)
            return 2
        return 0
    data = export_tokens(brand) if args.format == "dtcg" else export_brand_json(catalog, brand.slug)
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
