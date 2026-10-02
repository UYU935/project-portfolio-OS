"""Manual Evidence -> AI review -> optional PENDING approval pilot."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile

from sqlalchemy import func, select

from portfolio_os.approval_pilot import (
    complete_review_pilot,
    fail_review_pilot,
    reserve_review_pilot,
)
from portfolio_os.config import Config
from portfolio_os.db import Store
from portfolio_os.diagnostics import inspect_runtime
from portfolio_os.domain import BudgetExhausted, RuleError, StaleState
from portfolio_os.models import Approval, Project
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
        pending = session.scalar(
            select(func.count()).select_from(Approval).where(
                Approval.project_id == project.id,
                Approval.status == "PENDING",
            )
        )
        if pending:
            raise RuleError("trial_project_already_has_pending_approval")
        project_id = project.id
        before = {
            "stage": project.stage,
            "mode": project.mode,
            "automation_enabled": project.automation_enabled,
            "human_slot": project.human_slot,
            "revision": project.revision,
        }

    snapshot = store.snapshot(project_id, policy)
    if not snapshot.get("evidence"):
        raise RuleError("evidence_required_for_approval_review")

    run_id = reserve_review_pilot(store, slug, snapshot, policy, cfg.model)
    provider = OpenAIProvider(cfg.api_key, cfg.model, cfg.root)
    try:
        result = await provider.evaluate(snapshot, policy)
        outcome = complete_review_pilot(store, run_id, snapshot, result, policy)
    except Exception as exc:
        fail_review_pilot(store, run_id, exc)
        raise

    with store.read() as session:
        project = session.get(Project, project_id)
        after = {
            "stage": project.stage,
            "mode": project.mode,
            "automation_enabled": project.automation_enabled,
            "human_slot": project.human_slot,
            "revision": project.revision,
        }
    if before != after:
        raise RuleError("approval_review_changed_project_state")

    return result, outcome, len(snapshot["evidence"])


def main() -> int:
    if os.environ.get("PORTFOLIO_APPROVAL_REVIEW_REQUESTED") != "1":
        return emit("blocked", "manual_acknowledgment_required", paid_requests_made=0)
    if os.environ.get("TRIAL_CONFIRM_SANITIZED") != "1":
        return emit("blocked", "sanitized_project_confirmation_required", paid_requests_made=0)
    if os.environ.get("TRIAL_CONFIRM_APPROVAL_PROPOSAL") != "1":
        return emit("blocked", "approval_proposal_confirmation_required", paid_requests_made=0)

    slug = os.environ.get("TRIAL_PROJECT_SLUG", "").strip()
    if not slug:
        return emit("blocked", "missing_trial_project_slug", paid_requests_made=0)

    ca = os.environ.get("DATABASE_CA_CERT", "").strip()
    if ca and ("-----BEGIN CERTIFICATE-----" not in ca or "PRIVATE KEY" in ca):
        return emit("blocked", "invalid_ca_certificate", paid_requests_made=0)

    try:
        with tempfile.TemporaryDirectory(prefix="portfolio-approval-review-") as directory:
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
                result, outcome, evidence_count = asyncio.run(run_one(cfg, store, slug))
            finally:
                store.engine.dispose()

        return emit(
            "ready",
            "evidence_review_recorded",
            paid_requests_made=1,
            evidence_count=evidence_count,
            recommendation=outcome["recommendation"],
            effective_recommendation=outcome["effective_recommendation"],
            approval_created=outcome["approval_created"],
            approval_action=outcome["approval_action"],
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
        return emit("blocked", "approval_review_internal_error", paid_requests_made=0)


if __name__ == "__main__":
    raise SystemExit(main())
