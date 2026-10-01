"""Regression tests use simulated exceptions only; no real credentials/network."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from portfolio_os.preflight_errors import ERROR_CODES, classify_error

SPEC = importlib.util.spec_from_file_location(
    "preflight_categories_wrapper", Path(__file__).resolve().parents[1] / "scripts" / "github_preflight.py"
)
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)


@pytest.mark.parametrize("message,state,expected", [
    ('root certificate file "/PRIVATE/root.crt" does not exist', None, "tls_ca_missing"),
    ("SSL error: certificate verify failed PRIVATE", None, "tls_certificate_rejected"),
    ('server certificate does not match host name "PRIVATE"', None, "tls_certificate_rejected"),
    ("PRIVATE", "28P01", "authentication_failed"),
    ("password authentication failed for user PRIVATE", None, "authentication_failed"),
    ("Tenant or user not found PRIVATE", None, "pooler_identity_rejected"),
    ("role PRIVATE is not permitted to log in", None, "login_disabled"),
    ("could not translate host name PRIVATE", None, "hostname_resolution_failed"),
    ("connection timeout expired PRIVATE", None, "connection_timeout"),
    ("connection refused PRIVATE", None, "connection_unavailable"),
    ("PRIVATE", "42501", "database_permission_denied"),
    ("PRIVATE", "42P01", "database_schema_missing"),
    ("PRIVATE", "3F000", "database_schema_missing"),
    ("PRIVATE", "08006", "connection_unavailable"),
    ("PRIVATE", "XX000", "database_error"),
    ("PRIVATE", None, "diagnostic_internal_error"),
])
def test_exception_categories_do_not_reveal_details(message, state, expected):
    cause = RuntimeError(message)
    cause.sqlstate = state
    exc = OperationalError("PRIVATE SQL", {"PRIVATE": "PRIVATE"}, cause)
    result = classify_error(exc)
    assert result == expected and result in ERROR_CODES
    assert "PRIVATE" not in result


def test_configuration_and_driver_categories():
    assert classify_error(ValueError("PRIVATE sslmode=verify-full"), configuration=True) == "tls_verification_required"
    assert classify_error(ValueError("PRIVATE"), configuration=True) == "configuration_invalid"
    assert classify_error(ImportError("PRIVATE")) == "driver_missing"


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORTFOLIO_PREFLIGHT_REQUESTED", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://worker:PRIVATE@example.invalid/db?sslmode=verify-full")
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_API")
    monkeypatch.delenv("DATABASE_CA_CERT", raising=False)
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)


def good_report():
    return {"database_connection_tested": True, "database": {
        "database_ready": True, "checks": dict.fromkeys(wrapper.CHECKS, True),
        "paid_calls_made": 0, "openai_access_tested": False, "database_write_tested": False,
    }}


@pytest.mark.parametrize("code", ["tls_ca_missing", "authentication_failed", "PRIVATE", ["PRIVATE"]])
def test_wrapper_only_releases_allowed_codes(configured, monkeypatch, capsys, code):
    report = {"error_code": code, "PRIVATE": "PRIVATE"}
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 2, json.dumps(report), "PRIVATE"))
    assert wrapper.main() == 2
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err
    assert json.loads(output.out)["reason"] == (code if isinstance(code, str) and code in ERROR_CODES else "diagnostic_internal_error")


@pytest.mark.parametrize("result", ["", "PRIVATE", "null", "[]"])
def test_empty_or_invalid_child_result_is_not_misreported(configured, monkeypatch, capsys, result):
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 2, result, "PRIVATE"))
    assert wrapper.main() == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "invalid_diagnostic_result"


def test_system_roots_are_used_without_weakening_tls(configured, monkeypatch, capsys, tmp_path):
    ca = tmp_path / "trusted-os-bundle.pem"
    ca.write_text("TEST ROOTS")
    monkeypatch.setattr(wrapper.ssl, "get_default_verify_paths", lambda: SimpleNamespace(cafile=str(ca)))
    def fake_run(args, **kw):
        assert kw["env"]["PGSSLROOTCERT"] == str(ca)
        assert kw["env"]["DATABASE_URL"].endswith("sslmode=verify-full")
        assert kw["env"]["OPENAI_API_KEY"] == ""
        return subprocess.CompletedProcess(args, 0, json.dumps(good_report()), "")
    monkeypatch.setattr(wrapper.subprocess, "run", fake_run)
    assert wrapper.main() == 0


def test_explicit_ca_is_not_replaced(configured, monkeypatch, capsys):
    monkeypatch.setenv("PGSSLROOTCERT", "/owner/explicit-ca.pem")
    monkeypatch.setattr(wrapper.ssl, "get_default_verify_paths", lambda: pytest.fail("must preserve explicit CA"))
    def fake_run(args, **kw):
        assert kw["env"]["PGSSLROOTCERT"] == "/owner/explicit-ca.pem"
        return subprocess.CompletedProcess(args, 2, '{"error_code":"tls_ca_missing"}', "PRIVATE")
    monkeypatch.setattr(wrapper.subprocess, "run", fake_run)
    assert wrapper.main() == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "tls_ca_missing"


def test_doctor_connection_failure_is_valid_json(configured, monkeypatch, capsys, tmp_path):
    from portfolio_os import cli
    cfg = SimpleNamespace(database_url="PRIVATE", model="", api_key="", policy=lambda: None)
    monkeypatch.setattr(cli.Config, "load", lambda: cfg)
    monkeypatch.setattr(cli, "codex_version", lambda: "not installed")
    def broken_store(*args):
        raise OperationalError(None, None, RuntimeError('root certificate file "/PRIVATE/root.crt" does not exist'))
    monkeypatch.setattr(cli, "Store", broken_store)
    assert cli.main(["doctor", "--connect"]) == 2
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err
    assert json.loads(output.out)["error_code"] == "tls_ca_missing"


def test_doctor_configuration_failure_does_not_print_inputs(configured, monkeypatch, capsys):
    from portfolio_os import cli
    monkeypatch.setattr(cli, "codex_version", lambda: "not installed")
    def bad_config():
        raise ValueError("PRIVATE")
    monkeypatch.setattr(cli.Config, "load", bad_config)
    assert cli.main(["doctor", "--connect"]) == 2
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err
    assert json.loads(output.out)["error_code"] == "configuration_invalid"
