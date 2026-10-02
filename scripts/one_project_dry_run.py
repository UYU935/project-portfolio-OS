"""One-project AI evaluation dry run.

Reads exactly one private project and makes one evaluation request.
No database writes, web search, Codex execution, scheduling, or external messaging.
Public output is intentionally minimal.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile

from sqlalchemy import select

from portfolio_os.config import Config
from portfolio_os.db import Store
from portfolio_os.diagnostics import inspect_runtime
from portfolio_os.domain import RuleError
from portfolio_os.models import Project
from portfolio_os.provider import OpenAIProvider, ProviderError


def emit(status: str, reason: str, **safe) -> int:
    payload = {"status": status, "reason": reason}
    payload.update(safe)
    print(json.dumps(payload, sort_keys=True))
    return 0 if status == "ready" else 2


async def evaluate_one(cfg: Config, store: Store, slug: str):
    preflight = inspect_runtime(store)
    counts = preflight.get("counts", {})
    if not preflight.get("database_ready"):
        raise RuleError("database_preflight_failed")
    if preflight.get("paused") is not True:
        raise RuleError("portfolio_must_remain_paused")
    if counts.get("enabled_count") != 0:
        raise RuleError("automation_must_be_disabled")
    policy = cfg.policy()
    with store.read() as session:
        project = session.scalar(select(Project).where(Project.slug == slug))
        if project is None:
            raise RuleError("trial_project_not_found")
        if project.mode == "ARCHIVED":
            raise RuleError("trial_project_archived")
        project_id = project.id
    snapshot = store.snapshot(project_id, policy)
    provider = OpenAIProvider(cfg.api_key, cfg.model, cfg.root)
    return await provider.evaluate(snapshot, policy)


def main() -> int:
    if os.environ.get("PORTFOLIO_ONE_PROJECT_TRIAL_REQUESTED") != "1":
        return emit("blocked", "manual_acknowledgment_required", paid_requests_made=0)
    if os.environ.get("TRIAL_CONFIRM_SANITIZED") != "1":
        return emit("blocked", "sanitized_project_confirmation_required", paid_requests_made=0)
    slug = os.environ.get("TRIAL_PROJECT_SLUG", "").strip()
    if not slug:
        return emit("blocked", "missing_trial_project_slug", paid_requests_made=0)
    ca = os.environ.get("DATABASE_CA_CERT", "").strip()
    if ca and ("-----BEGIN CERTIFICATE-----" not in ca or "PRIVATE KEY" in ca):
        return emit("blocked", "invalid_ca_certificate", paid_requests_made=0)
    try:
        with tempfile.TemporaryDirectory(prefix="portfolio-trial-") as directory:
            if ca:
                cert_path = Path(directory) / "root.crt"
                cert_path.write_text(ca + "\n", encoding="utf-8")
                cert_path.chmod(0o600)
                os.environ["PGSSLROOTCERT"] = str(cert_path)
            cfg = Config.load()
            if not cfg.api_key or not cfg.model:
                return emit("blocked", "missing_openai_configuration", paid_requests_made=0)
            store = Store(cfg.database_url)
            try:
                result = asyncio.run(evaluate_one(cfg, store, slug))
            finally:
                store.engine.dispose()
        return emit(
            "ready",
            "one_project_read_only_evaluation_completed",
            paid_requests_made=1,
            recommendation=result.value.recommendation,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
    except ProviderError as exc:
        return emit("blocked", exc.code, paid_requests_made=1)
    except RuleError as exc:
        return emit("blocked", str(exc), paid_requests_made=0)
    except Exception:
        return emit("blocked", "trial_internal_error", paid_requests_made=0)


if __name__ == "__main__":
    raise SystemExit(main())
