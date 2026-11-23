# Security policy

## Supported versions

Security fixes are released for the latest minor version.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub's
[private vulnerability reporting](https://github.com/Alejandro-Cuautli/brand-kit-mcp/security/advisories/new)
("Security" tab, then "Report a vulnerability"). Include the version, how to reproduce the problem
and its impact. Reports are handled on a best-effort basis; you will get an answer as soon as possible.

## Scope

Particularly relevant reports:

- Reading files outside a brand folder (path traversal, symlinks, race conditions).
- Serving assets marked `quarantined`.
- Bypassing the Bearer token in HTTP mode.
- Code execution or resource exhaustion through `brand.yaml` or asset files.
- Absolute file-system paths or other host details leaking into tool results.

## Hardening tips

- Keep HTTP mode bound to `127.0.0.1`, or set `BRAND_KIT_TOKEN` and put it behind TLS.
- Mount the brands folder read-only (`-v ./brands:/brands:ro`).
- Treat SVG files as untrusted; sanitize them before embedding them in web pages.
