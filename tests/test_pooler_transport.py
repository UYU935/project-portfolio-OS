"""Regression: a proxy backend's pg_stat_ssl is not the client socket TLS state."""
from contextlib import nullcontext
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from types import SimpleNamespace as NS
import subprocess

import pytest
from portfolio_os.diagnostics import (
    EXPECTED_TABLES, ROLE_SQL, TABLE_SQL, STATE_SQL,
    assess, client_tls_verified, inspect_runtime,
)


def connection(*, tls=True, mode="verify-full", closed=False):
    raw = NS(closed=closed, pgconn=NS(ssl_in_use=tls),
             info=NS(get_parameters=lambda: {"sslmode": mode, "host": "PRIVATE_HOST"}))
    return NS(connection=NS(driver_connection=raw))


def data(backend=False):
    role = {"can_login": True, "privileged": False, "runtime_member": True,
            "schema_create": False, "anon_access": False, "authenticated_access": False,
            "backend_tls": backend}
    tables = [{"name": name, "rls": True, "owns_table": False, "can_select": True,
               "can_insert": True, "can_update": name != "decisions", "dangerous": False}
              for name in EXPECTED_TABLES]
    state = {"control_rows": 1, "paused": True, "human_count": 0}
    return role, tables, state


def test_verified_live_client():
    assert client_tls_verified(connection()) is True


@pytest.mark.parametrize("tls", [False, None, 1, "true"])
def test_tls_flag_must_be_true(tls):
    assert client_tls_verified(connection(tls=tls)) is False


@pytest.mark.parametrize("mode", ["require", "prefer", "verify-ca", "disable", None])
def test_hostname_verification_is_required(mode):
    assert client_tls_verified(connection(mode=mode)) is False


@pytest.mark.parametrize("closed", [True, None, "false"])
def test_closed_or_unknown_connection_is_rejected(closed):
    assert client_tls_verified(connection(closed=closed)) is False


def test_missing_driver_telemetry_is_not_assumed_secure():
    assert client_tls_verified(NS(connection=NS(driver_connection=NS()))) is False


def test_driver_exception_does_not_escape_or_leak(capsys):
    conn = connection()
    def broken():
        raise RuntimeError("PRIVATE_PASSWORD")
    conn.connection.driver_connection.info.get_parameters = broken
    assert client_tls_verified(conn) is False
    assert "PRIVATE" not in str(capsys.readouterr())


@pytest.mark.parametrize("backend", [False, None, True])
def test_client_and_backend_are_separate(backend):
    role, tables, state = data(backend)
    role["tls"] = True
    report = assess(role, tables, state)
    assert report["database_ready"] is True
    assert report["transport"] == {"client_tls_verified": True,
        "postgres_backend_tls": backend, "end_to_end_tls_verified": False}


def test_secure_backend_does_not_prove_secure_client():
    role, tables, state = data(True)
    role["tls"] = False
    assert assess(role, tables, state)["database_ready"] is False


@pytest.mark.parametrize("value", ["true", 1, None])
def test_assessor_tls_is_not_truthiness(value):
    role, tables, state = data()
    role["tls"] = value
    assert assess(role, tables, state)["checks"]["encrypted_connection"] is False


@pytest.mark.parametrize("client,backend", [(True,False), (False,True)])
def test_inspection_uses_actual_driver_not_server_row(client, backend):
    role, tables, state = data(backend)
    queries = []
    conn = connection(tls=client)
    conn.begin = lambda: nullcontext()
    def execute(statement):
        sql = str(statement)
        queries.append(sql)
        if sql == ROLE_SQL:
            return NS(mappings=lambda: NS(one=lambda: role.copy()))
        if sql == TABLE_SQL:
            return NS(mappings=lambda: tables)
        if sql == STATE_SQL:
            return NS(mappings=lambda: NS(one=lambda: state.copy()))
        assert sql in ("SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '5s'")
        return None
    conn.execute = execute
    store = NS(demo=False, engine=NS(connect=lambda: nullcontext(conn)))
    result = inspect_runtime(store)
    assert result["checks"]["encrypted_connection"] is client
    assert result["transport"]["postgres_backend_tls"] is backend
    assert queries == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '5s'",
                       ROLE_SQL, TABLE_SQL, STATE_SQL]


def wrapper():
    spec = spec_from_file_location("tls_wrapper", Path(__file__).resolve().parents[1] / "scripts/github_preflight.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("backend", [False, None, "PRIVATE_PASSWORD", True])
def test_public_transport_output_is_sanitized(backend, capsys):
    mod = wrapper()
    mod.emit("ready", "read_only_database_checks_passed", transport={
        "client_tls_verified": True, "postgres_backend_tls": backend,
        "end_to_end_tls_verified": True, "PRIVATE_HOST": "PRIVATE_PASSWORD"})
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    result = json.loads(output)
    assert result["transport"]["end_to_end_tls_verified"] is False
    assert ("warnings" in result) is (backend is not True)


def test_wrapper_surfaces_backend_warning_without_raw_output(monkeypatch, tmp_path, capsys):
    mod = wrapper()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORTFOLIO_PREFLIGHT_REQUESTED", "1")
    monkeypatch.setenv("DATABASE_URL", "PRIVATE_DSN")
    monkeypatch.delenv("DATABASE_CA_CERT", raising=False)
    role, tables, state = data(False)
    role["tls"] = True
    report = {"database_connection_tested": True, "database": assess(role, tables, state)}
    def fake_run(args, **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["env"]["OPENAI_API_KEY"] == ""
        return subprocess.CompletedProcess(args, 0, json.dumps(report), "PRIVATE_PASSWORD")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main() == 0
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    result = json.loads(output)
    assert result["warnings"] == ["postgres_backend_tls_not_confirmed"]
    assert result["transport"]["client_tls_verified"] is True
