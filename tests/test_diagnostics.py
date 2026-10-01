import json
from pathlib import Path
import pytest
from portfolio_os.config import Config
from portfolio_os.diagnostics import EXPECTED_TABLES, assess, inspect_runtime
from portfolio_os.domain import RuleError

def fixture_data():
    role = {"can_login": True, "privileged": False, "runtime_member": True,
            "schema_create": False, "anon_access": False, "authenticated_access": False, "tls": True}
    tables = [{"name": name, "rls": True, "owns_table": False, "can_select": True,
               "can_insert": True, "can_update": name != "decisions", "dangerous": False}
              for name in sorted(EXPECTED_TABLES)]
    state = {"control_rows": 1, "paused": True, "project_count": 7,
             "enabled_count": 0, "human_count": 0, "run_count": 0}
    return role, tables, state

def test_paused_safe_database_is_ready_but_not_running():
    result = assess(*fixture_data())
    assert result["database_ready"]
    assert result["paused"] is True
    assert result["paid_calls_made"] == 0
    assert result["openai_access_tested"] is False
    assert result["database_write_tested"] is False

@pytest.mark.parametrize("field,value", [
    ("can_login", False), ("privileged", True), ("runtime_member", False),
    ("schema_create", True), ("anon_access", True), ("authenticated_access", True), ("tls", False),
])
def test_unsafe_role_fails_closed(field, value):
    role, tables, state = fixture_data()
    role[field] = value
    assert assess(role, tables, state)["database_ready"] is False

@pytest.mark.parametrize("field,value", [
    ("rls", False), ("owns_table", True), ("can_select", False),
    ("can_insert", False), ("dangerous", True),
])
def test_table_permissions_checked_individually(field, value):
    role, tables, state = fixture_data()
    tables[0][field] = value
    assert assess(role, tables, state)["database_ready"] is False

def test_audit_updates_forbidden():
    role, tables, state = fixture_data()
    next(t for t in tables if t["name"] == "decisions")["can_update"] = True
    assert "required_grants" in assess(role, tables, state)["failed_checks"]

def test_missing_table_fails_closed():
    role, tables, state = fixture_data()
    assert not assess(role, tables[:-1], state)["database_ready"]

@pytest.mark.parametrize("field,value", [("control_rows", 0), ("control_rows", 2), ("paused", None), ("human_count", 4)])
def test_invalid_state_fails_closed(field, value):
    role, tables, state = fixture_data()
    state[field] = value
    assert not assess(role, tables, state)["database_ready"]

def test_raw_values_are_not_exported():
    role, tables, state = fixture_data()
    role["connection_string"] = "SECRET_DSN"
    tables[0]["private_brief"] = "PRIVATE_BRIEF"
    state["project_name"] = "PRIVATE_PROJECT"
    output = json.dumps(assess(role, tables, state))
    assert all(value not in output for value in ("SECRET_DSN", "PRIVATE_BRIEF", "PRIVATE_PROJECT"))

def test_config_repr_hides_credentials():
    cfg = Config(Path("."), "SECRET_DSN", "model", "SECRET_KEY")
    assert "SECRET_DSN" not in repr(cfg)
    assert "SECRET_KEY" not in repr(cfg)

def test_preflight_refuses_sqlite(store):
    with pytest.raises(RuleError):
        inspect_runtime(store)

@pytest.mark.parametrize("parameter", ["user", "password", "dbname", "port", "options"])
def test_connection_query_overrides_rejected(monkeypatch, tmp_path, parameter):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://worker:pw@db.example.com/postgres?sslmode=verify-full&" + parameter + "=override")
    with pytest.raises(RuleError):
        Config.load()
