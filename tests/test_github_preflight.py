import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location(
    "github_preflight", Path(__file__).resolve().parents[1] / "scripts" / "github_preflight.py"
)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORTFOLIO_PREFLIGHT_REQUESTED", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://worker:PRIVATE_PASSWORD@example.invalid/db?sslmode=verify-full")
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_API_KEY")
    monkeypatch.delenv("DATABASE_CA_CERT", raising=False)


def good_report():
    return {"database_connection_tested": True, "database": {
        "database_ready": True,
        "checks": {key: True for key in preflight.CHECKS},
        "paid_calls_made": 0, "openai_access_tested": False,
        "database_write_tested": False,
        "counts": {"PRIVATE_CUSTOMER": 123},
    }}


def test_manual_ack_required(monkeypatch, capsys):
    monkeypatch.delenv("PORTFOLIO_PREFLIGHT_REQUESTED", raising=False)
    assert preflight.main() == 2
    assert "manual_acknowledgment_required" in capsys.readouterr().out


def test_missing_database_url(configured, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL")
    assert preflight.main() == 2
    assert "missing_database_url" in capsys.readouterr().out


def test_dotenv_rejected(configured, capsys):
    Path(".env").write_text("PRIVATE_PASSWORD")
    assert preflight.main() == 2
    assert "unexpected_local_env_file" in capsys.readouterr().out


def test_ready_output_is_allowlisted(configured, monkeypatch, capsys):
    def fake_run(args, **kwargs):
        assert args[-2:] == ["doctor", "--connect"]
        assert kwargs["env"]["OPENAI_API_KEY"] == ""
        assert kwargs["env"]["OPENAI_MODEL"] == ""
        assert kwargs["capture_output"] is True
        report = good_report()
        report["database"]["checks"]["PRIVATE_PASSWORD"] = True
        return subprocess.CompletedProcess(args, 0, json.dumps(report), "PRIVATE_PASSWORD")
    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    assert preflight.main() == 0
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and "counts" not in output
    assert json.loads(output)["status"] == "ready"


@pytest.mark.parametrize("case", ["invalid_json", "exception", "timeout", "exit_code", "missing_check", "nonboolean", "paid", "write"])
def test_failures_do_not_leak(configured, monkeypatch, capsys, case):
    def fake_run(args, **kwargs):
        if case == "exception":
            raise RuntimeError("PRIVATE_PASSWORD")
        if case == "timeout":
            raise subprocess.TimeoutExpired("PRIVATE_PASSWORD", 90)
        report = good_report()
        if case == "missing_check":
            del report["database"]["checks"]["rls_enabled"]
        if case == "nonboolean":
            report["database"]["checks"]["rls_enabled"] = "PRIVATE_PASSWORD"
        if case == "paid":
            report["database"]["paid_calls_made"] = 1
        if case == "write":
            report["database"]["database_write_tested"] = True
        stdout = "PRIVATE_PASSWORD" if case == "invalid_json" else json.dumps(report)
        return subprocess.CompletedProcess(args, 1 if case == "exit_code" else 0, stdout, "PRIVATE_PASSWORD")
    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    assert preflight.main() == 2
    assert "PRIVATE" not in capsys.readouterr().out


def test_ca_tempfile_is_removed(configured, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_CA_CERT", "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----")
    saved = []
    def fake_run(args, **kwargs):
        path = Path(kwargs["env"]["PGSSLROOTCERT"])
        saved.append(path)
        assert path.exists()
        assert path.stat().st_mode & 0o777 == 0o600
        assert "DATABASE_CA_CERT" not in kwargs["env"]
        return subprocess.CompletedProcess(args, 0, json.dumps(good_report()), "")
    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    assert preflight.main() == 0
    assert not saved[0].exists()


def test_private_key_not_accepted_as_ca(configured, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_CA_CERT", "-----BEGIN PRIVATE KEY-----\nPRIVATE_PASSWORD")
    assert preflight.main() == 2
    output = capsys.readouterr().out
    assert "invalid_ca_certificate" in output and "PRIVATE_PASSWORD" not in output
