"""Manual, read-only doctor wrapper. Public logs contain allowlisted results only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CHECKS = (
    "dedicated_login", "non_privileged_role", "runtime_membership",
    "no_schema_creation", "private_schema", "encrypted_connection",
    "expected_tables", "rls_enabled", "not_table_owner",
    "no_delete_truncate_trigger", "required_grants", "control_initialized",
    "human_limit",
)


def emit(status: str, reason: str, checks: dict | None = None) -> int:
    # Never include credentials, exception messages, raw doctor output or counts.
    result = {"status": status, "reason": reason, "paid_calls_made": 0}
    if checks is not None:
        result["checks"] = {key: checks.get(key) is True for key in CHECKS}
    print(json.dumps(result, sort_keys=True))
    return 0 if status == "ready" else 2


def main() -> int:
    if os.environ.get("PORTFOLIO_PREFLIGHT_REQUESTED") != "1":
        return emit("blocked", "manual_acknowledgment_required")
    if not os.environ.get("DATABASE_URL", "").strip():
        return emit("blocked", "missing_database_url")
    if Path(".env").exists():
        return emit("blocked", "unexpected_local_env_file")
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = ""
    env["OPENAI_MODEL"] = ""
    env["PORTFOLIO_WORKSPACE"] = str(Path.cwd())
    ca = env.pop("DATABASE_CA_CERT", "").strip()
    if ca and ("-----BEGIN CERTIFICATE-----" not in ca or "PRIVATE KEY" in ca):
        return emit("blocked", "invalid_ca_certificate")
    try:
        with tempfile.TemporaryDirectory(prefix="portfolio-preflight-") as directory:
            if ca:
                cert_path = Path(directory) / "root.crt"
                cert_path.write_text(ca + "\n", encoding="utf-8")
                cert_path.chmod(0o600)
                env["PGSSLROOTCERT"] = str(cert_path)
            completed = subprocess.run(
                [sys.executable, "-m", "portfolio_os.cli", "doctor", "--connect"],
                env=env, capture_output=True, text=True, timeout=90, check=False,
            )
            # Captured stdout/stderr may contain secrets; never relay them.
            if completed.returncode not in (0, 2):
                return emit("blocked", "diagnostic_process_failed")
            report = json.loads(completed.stdout)
            database = report.get("database", {})
            if not isinstance(database, dict):
                return emit("blocked", "invalid_diagnostic_result")
            checks = database.get("checks", {})
            if not isinstance(checks, dict):
                return emit("blocked", "invalid_diagnostic_result")
            safe_checks = {key: checks.get(key) is True for key in CHECKS}
            ready = (
                completed.returncode == 0
                and report.get("database_connection_tested") is True
                and database.get("database_ready") is True
                and all(safe_checks.values())
                and database.get("paid_calls_made") == 0
                and database.get("openai_access_tested") is False
                and database.get("database_write_tested") is False
            )
            return emit("ready" if ready else "blocked",
                        "read_only_database_checks_passed" if ready else "database_checks_failed",
                        safe_checks)
    except subprocess.TimeoutExpired:
        return emit("blocked", "diagnostic_timeout")
    except Exception:
        # Intentional catch at the public-log boundary; no exception text escapes.
        return emit("blocked", "diagnostic_failed")


if __name__ == "__main__":
    raise SystemExit(main())
