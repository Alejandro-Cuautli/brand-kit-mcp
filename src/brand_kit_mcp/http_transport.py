"""Streamable HTTP transport with optional Bearer-token authentication."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


class BearerAuthMiddleware:
    """Reject HTTP requests that do not carry ``Authorization: Bearer <token>``.

    The comparison is constant-time (both sides are hashed first, so the length does not leak
    either). Paths in ``exempt_paths`` (the health check) are served without a token.
    """

    def __init__(self, app: ASGIApp, token: str, exempt_paths: Iterable[str] = ("/health",)):
        if not token:
            raise ValueError("token must not be empty")
        self.app = app
        self._expected = _digest(token.encode("utf-8"))
        self.exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        if self._authorized(scope):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"error": "unauthorized", "detail": "missing or invalid bearer token"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b'Bearer realm="brand-kit-mcp"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _authorized(self, scope: Scope) -> bool:
        header = b""
        for name, value in scope.get("headers") or []:
            if name.lower() == b"authorization":
                header = value
                break
        scheme, _, credential = header.partition(b" ")
        if scheme.lower() != b"bearer" or not credential.strip():
            # still burn one comparison so timing does not reveal which check failed
            hmac.compare_digest(self._expected, _digest(b""))
            return False
        return hmac.compare_digest(self._expected, _digest(credential.strip()))


def build_http_app(
    server: MCPServer,
    *,
    host: str = "127.0.0.1",
    token: str | None = None,
    allowed_hosts: list[str] | None = None,
) -> ASGIApp:
    """The ASGI app: MCP at ``/mcp``, health check at ``/health``, Bearer auth when ``token`` is set."""
    security = None
    if allowed_hosts:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[f"http://{h}" for h in allowed_hosts] + [f"https://{h}" for h in allowed_hosts],
        )
    app: ASGIApp = server.streamable_http_app(host=host, transport_security=security)
    if token:
        app = BearerAuthMiddleware(app, token)
    return app


def run_http(
    server: MCPServer,
    *,
    host: str,
    port: int,
    token: str | None,
    allowed_hosts: list[str] | None = None,
    log_level: str = "info",
) -> None:
    import uvicorn

    if token is not None and len(token) < 16:
        print("warning: BRAND_KIT_TOKEN is shorter than 16 characters", file=sys.stderr)
    if not token and host not in LOOPBACK_HOSTS:
        print(
            f"warning: listening on {host} without authentication. Set BRAND_KIT_TOKEN or bind to 127.0.0.1.",
            file=sys.stderr,
        )
    app = build_http_app(server, host=host, token=token, allowed_hosts=allowed_hosts)
    auth = "bearer token required" if token else "no authentication"
    print(f"brand-kit-mcp: streamable HTTP on http://{host}:{port}/mcp ({auth})", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level=log_level, server_header=False)
