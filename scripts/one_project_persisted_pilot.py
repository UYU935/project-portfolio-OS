"""Manual one-project pilot that persists only the evaluation audit record."""
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
from portfolio_os.domain import BudgetExhausted, RuleError, StaleState
from portfolio_os.models import Project
from portfolio_os.pilot import complete_pilot, fail_pilot, reserve_pilot
from portfolio_os.provider import OpenAIProvider, ProviderError


def emit(status: str, reason: str, **safe) -> int:
    payload = {"status": status, "reason": reason}
    payload.update(safe)
    print(json.dumps(payload, sort_keys=True))
    return 0 if status == "ready" else 2


async def run_one(cfg: Config, store: Store, slug: str):
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
        if project.automation_enabled:
            raise RuleError("trial_project_automation_must_be_disabled")
        project_id = project.id
        before = {
            "revision": project.revision,
            "stage": project.stage,
            "mode": project.mode,
            "automation_enabled": project.automation_enabled,
            "human_slot": project.human_slot,
        }

    snapshot = store.snapshot(project_id, policy)
    run_id = reserve_pilot(store, slug, snapshot, policy, cfg.model)
    provider = OpenAIProvider(cfg.api_key, cfg.model, cfg.root)

    try:
        result = await provider.evaluate(snapshot, policy)
    except Exception as exc:
        fail_pilot(store, run_id, exc)
        raise

    complete_pilot(store, run_id, snapshot, result)

    with store.read() as session:
        project = session.get(Project, project_id)
        after = {
            "revision": project.revision,
            "stage": project.stage,
            "mode": project.mode,
            "automation_enabled": project.automation_enabled,
            "human_slot": project.human_slot,
        }
    if before != after:
        raise RuleError("pilot_changed_project_state")
    return result


def main() -> int:
    if os.environ.get("PORTFOLIO_PERSISTED_PILOT_REQUESTED") != "1":
        return emit("blocked", "manual_acknowledgment_required", paid_requests_made=0)
    if os.environ.get("TRIAL_CONFIRM_SANITIZED") != "1":
        return emit("blocked", "sanitized_project_confirmation_required", paid_requests_made=0)
    if os.environ.get("TRIAL_CONFIRM_PERSIST") != "1":
        return emit("blocked", "persist_confirmation_required", paid_requests_made=0)

    slug = os.environ.get("TRIAL_PROJECT_SLUG", "").strip()
    if not slug:
        return emit("blocked", "missing_trial_project_slug", paid_requests_made=0)

    ca = os.environ.get("DATABASE_CA_CERT", "").strip()
    if ca and ("-----BEGIN CERTIFICATE-----" not in ca or "PRIVATE KEY" in ca):
        return emit("blocked", "invalid_ca_certificate", paid_requests_made=0)

    try:
        with tempfile.TemporaryDirectory(prefix="portfolio-persisted-pilot-") as directory:
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
                result = asyncio.run(run_one(cfg, store, slug))
            finally:
                store.engine.dispose()

        return emit(
            "ready",
            "one_project_persisted_evaluation_completed",
            paid_requests_made=1,
            recommendation=result.value.recommendation,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            project_state_changed=False,
        )
    except ProviderError as exc:
        return emit("blocked", exc.code, paid_requests_made=1)
    except BudgetExhausted as exc:
        return emit("blocked", str(exc), paid_requests_made=0)
    except (RuleError, StaleState) as exc:
        return emit("blocked", str(exc), paid_requests_made=0)
    except Exception:
        return emit("blocked", "pilot_internal_error", paid_requests_made=0)


if __name__ == "__main__":
    raise SystemExit(main())
