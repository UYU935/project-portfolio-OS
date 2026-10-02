import importlib.util
from pathlib import Path
import httpx

SPEC = importlib.util.spec_from_file_location(
    "openai_auth_preflight", Path(__file__).resolve().parents[1] / "scripts" / "openai_auth_preflight.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def transport(status=200, ids=None):
    ids = ids or ["gpt-6-sol"]
    def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers.get("authorization", "").startswith("Bearer ")
        return httpx.Response(status, json={"data": [{"id": x} for x in ids]})
    return httpx.MockTransport(handler)


def test_ready_when_auth_and_model_are_available(capsys):
    assert mod.check("secret", "gpt-6-sol", transport=transport()) == 0
    out = capsys.readouterr().out
    assert '"status": "ready"' in out
    assert "secret" not in out


def test_model_missing_fails_closed(capsys):
    assert mod.check("secret", "gpt-6-sol", transport=transport(ids=["other"])) == 2
    assert "openai_model_not_listed" in capsys.readouterr().out


def test_auth_failure_does_not_leak(capsys):
    assert mod.check("secret", "gpt-6-sol", transport=transport(status=401)) == 2
    out = capsys.readouterr().out
    assert "openai_authentication_failed" in out and "secret" not in out
