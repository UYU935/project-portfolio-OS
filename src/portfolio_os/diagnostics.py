"""Read-only runtime preflight. Never call a model or disclose business records."""
from __future__ import annotations

from sqlalchemy import text
from .db import Store
from .domain import RuleError

EXPECTED_TABLES = frozenset({"control", "projects", "agent_runs", "approvals", "decisions", "evidence", "tasks"})
ROLE_SQL = """
SELECT r.rolcanlogin AS can_login,
       (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication) AS privileged,
       pg_has_role(current_user, 'portfolio_os_runtime', 'USAGE') AS runtime_member,
       has_schema_privilege(current_user, 'portfolio_os', 'CREATE') AS schema_create,
       has_schema_privilege('anon', 'portfolio_os', 'USAGE') AS anon_access,
       has_schema_privilege('authenticated', 'portfolio_os', 'USAGE') AS authenticated_access,
       coalesce((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()), false) AS tls
FROM pg_roles r WHERE r.rolname=current_user
"""
TABLE_SQL = """
SELECT c.relname AS name, c.relrowsecurity AS rls,
       pg_has_role(current_user,c.relowner,'USAGE') AS owns_table,
       has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
       has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
       has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,
       (has_table_privilege(current_user,c.oid,'DELETE') OR
        has_table_privilege(current_user,c.oid,'TRUNCATE') OR
        has_table_privilege(current_user,c.oid,'TRIGGER')) AS dangerous
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='portfolio_os' AND c.relkind='r'
"""
STATE_SQL = """
SELECT (SELECT count(*) FROM portfolio_os.control) AS control_rows,
       (SELECT paused FROM portfolio_os.control WHERE id=1) AS paused,
       (SELECT count(*) FROM portfolio_os.projects) AS project_count,
       (SELECT count(*) FROM portfolio_os.projects WHERE automation_enabled) AS enabled_count,
       (SELECT count(*) FROM portfolio_os.projects WHERE mode='HUMAN') AS human_count,
       (SELECT count(*) FROM portfolio_os.agent_runs) AS run_count
"""

def assess(role: dict, tables: list[dict], state: dict) -> dict:
    expected = [row for row in tables if row.get("name") in EXPECTED_TABLES]
    checks = {
        "dedicated_login": bool(role.get("can_login")),
        "non_privileged_role": role.get("privileged") is False,
        "runtime_membership": bool(role.get("runtime_member")),
        "no_schema_creation": role.get("schema_create") is False,
        "private_schema": role.get("anon_access") is False and role.get("authenticated_access") is False,
        "encrypted_connection": bool(role.get("tls")),
        "expected_tables": {row.get("name") for row in tables} == EXPECTED_TABLES,
        "rls_enabled": len(expected) == 7 and all(row.get("rls") is True for row in expected),
        "not_table_owner": len(expected) == 7 and all(row.get("owns_table") is False for row in expected),
        "no_delete_truncate_trigger": len(expected) == 7 and all(row.get("dangerous") is False for row in expected),
        "required_grants": len(expected) == 7 and all(row.get("can_select") is True and row.get("can_insert") is True and row.get("can_update") is (row["name"] != "decisions") for row in expected),
        "control_initialized": state.get("control_rows") == 1 and type(state.get("paused")) is bool,
        "human_limit": type(state.get("human_count")) is int and 0 <= state["human_count"] <= 3,
    }
    counts = {key: state[key] for key in ("project_count", "enabled_count", "human_count", "run_count") if type(state.get(key)) is int and state[key] >= 0}
    return {"database_ready": all(checks.values()), "checks": checks,
            "failed_checks": [name for name, passed in checks.items() if not passed],
            "paused": state.get("paused") if type(state.get("paused")) is bool else None,
            "counts": counts, "paid_calls_made": 0, "openai_access_tested": False, "database_write_tested": False}

def inspect_runtime(store: Store) -> dict:
    if store.demo:
        raise RuleError("Runtime preflight requires PostgreSQL, not a demo database")
    with store.engine.connect() as conn, conn.begin():
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '5s'"))
        role = dict(conn.execute(text(ROLE_SQL)).mappings().one())
        tables = [dict(row) for row in conn.execute(text(TABLE_SQL)).mappings()]
        state = dict(conn.execute(text(STATE_SQL)).mappings().one())
        return assess(role, tables, state)
