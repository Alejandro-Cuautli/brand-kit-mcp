from __future__ import annotations

import json

import pytest

from brand_kit_mcp import __version__
from brand_kit_mcp.cli import main
from brand_kit_mcp.http_transport import BearerAuthMiddleware

pytestmark = pytest.mark.anyio


class Recorder:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def request(app, path="/mcp", headers=None, scope_type="http"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {"type": scope_type, "path": path, "headers": headers or []}
    await app(scope, receive, send)
    return sent[0]["status"] if sent else None, dict(sent[0].get("headers", [])) if sent else {}


class TestBearer:
    TOKEN = "s3cret-token-for-tests"

    async def test_missing_token(self):
        inner = Recorder()
        status, headers = await request(BearerAuthMiddleware(inner, self.TOKEN))
        assert status == 401 and not inner.called
        assert headers[b"www-authenticate"].startswith(b"Bearer")

    @pytest.mark.parametrize(
        "value", [b"Bearer wrong", b"Basic czNjcmV0", b"Bearer", b"bearer s3cret-token-for-test", b""]
    )
    async def test_wrong_token(self, value):
        inner = Recorder()
        status, _ = await request(BearerAuthMiddleware(inner, self.TOKEN), headers=[(b"authorization", value)])
        assert status == 401 and not inner.called

    async def test_valid_token(self):
        inner = Recorder()
        header = [(b"authorization", b"Bearer " + self.TOKEN.encode())]
        status, _ = await request(BearerAuthMiddleware(inner, self.TOKEN), headers=header)
        assert status == 200 and inner.called

    async def test_scheme_is_case_insensitive(self):
        inner = Recorder()
        header = [(b"Authorization", b"bearer " + self.TOKEN.encode())]
        status, _ = await request(BearerAuthMiddleware(inner, self.TOKEN), headers=header)
        assert status == 200

    async def test_health_is_exempt(self):
        inner = Recorder()
        status, _ = await request(BearerAuthMiddleware(inner, self.TOKEN), path="/health")
        assert status == 200

    async def test_lifespan_passes_through(self):
        inner = Recorder()
        await request(BearerAuthMiddleware(inner, self.TOKEN), scope_type="lifespan")
        assert inner.called

    def test_empty_token_rejected(self):
        with pytest.raises(ValueError):
            BearerAuthMiddleware(Recorder(), "")


class TestCli:
    def test_version(self, capsys):
        with pytest.raises(SystemExit):
            main(["--version"])
        assert __version__ in capsys.readouterr().out

    def test_validate_ok(self, examples_dir, capsys):
        assert main(["validate", str(examples_dir)]) == 0
        out = capsys.readouterr().out
        assert "ok  tidewren-trail (parent: tidewren)" in out
        assert "2 brands" in out

    def test_validate_errors(self, make_brand, capsys):
        make_brand("acme", "name: Acme\ntokens:\n  color.ink: blue\n")
        assert main(["validate", str(make_brand.root)]) == 2
        assert "color tokens must be hex" in capsys.readouterr().err

    def test_validate_uses_env(self, examples_dir, monkeypatch, capsys):
        monkeypatch.setenv("BRAND_KIT_DIR", str(examples_dir))
        assert main(["validate"]) == 0

    def test_export_dtcg(self, examples_dir, capsys):
        assert main(["export", "tidewren", "--brands-dir", str(examples_dir)]) == 0
        tree = json.loads(capsys.readouterr().out)
        assert tree["color"]["accent"]["$value"]["hex"] == "#F2A541"

    def test_export_brand_json(self, examples_dir, capsys):
        assert main(["export", "tidewren", "--brands-dir", str(examples_dir), "--format", "brand-json"]) == 0
        assert json.loads(capsys.readouterr().out)["brand"]["name"] == "Tidewren"

    def test_export_css(self, examples_dir, capsys):
        assert main(["export", "tidewren-trail", "--brands-dir", str(examples_dir), "--format", "css"]) == 0
        assert "--color-accent: #5B8C3A;" in capsys.readouterr().out

    def test_export_unknown_brand(self, examples_dir, capsys):
        assert main(["export", "nope", "--brands-dir", str(examples_dir)]) == 2
        assert "unknown brand" in capsys.readouterr().err

    def test_serve_missing_dir(self, tmp_path, capsys):
        assert main(["--brands-dir", str(tmp_path / "missing")]) == 2
        assert "brands directory not found" in capsys.readouterr().err
