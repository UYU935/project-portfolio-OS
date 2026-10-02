"""Record-only one-project pilot.

This module may persist one model evaluation and its audit trail, but it never
changes project stage/mode/automation, creates tasks/approvals, or triggers work.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from .db import Store, log
from .domain import BudgetExhausted, Policy, RuleError, StaleState, digest
from .models import Project, Run, now
from .provider import ProviderError, Result


def reserve_pilot(store: Store, slug: str, snapshot: dict, policy: Policy, model: str) -> str:
    """Reserve exactly one paid request while leaving the project unchanged."""
    with store.tx() as session:
        project = session.scalar(select(Project).where(Project.slug == slug))
        if project is None:
            raise RuleError("trial_project_not_found")
        if project.mode == "ARCHIVED":
            raise RuleError("trial_project_archived")
        if project.revision != snapshot["project"]["revision"]:
            raise StaleState("trial_project_changed_before_reservation")
        if project.automation_enabled:
            raise RuleError("trial_project_automation_must_be_disabled")

        open_runs = session.scalar(
            select(func.count()).select_from(Run).where(
                Run.project_id == project.id,
                Run.status.in_(["RESERVED", "UNCERTAIN"]),
            )
        )
        if open_runs:
            raise RuleError("trial_project_has_open_run")

        day = datetime.now(timezone.utc).date().isoformat()
        used = session.scalar(
            select(func.count()).select_from(Run).where(
                Run.day == day,
                Run.provider == "OPENAI",
            )
        )
        if used >= policy.max_paid_requests_per_day:
            raise BudgetExhausted("daily_request_reservation_limit_reached")

        run = Run(
            project_id=project.id,
            kind="REVIEW",
            provider="OPENAI",
            model=model,
            day=day,
            input_hash=digest(snapshot),
            policy_hash=digest(policy.model_dump()),
            output={"pilot_record_only": True},
        )
        session.add(run)
        session.flush()
        log(
            session,
            "PILOT_EVALUATION_RESERVED",
            {
                "run_id": run.id,
                "project_revision": project.revision,
                "record_only": True,
            },
            project.id,
            "OWNER",
        )
        return run.id


def complete_pilot(store: Store, run_id: str, snapshot: dict, result: Result) -> None:
    """Persist the evaluation without applying its recommendation."""
    with store.tx() as session:
        run = session.get(Run, run_id)
        if run is None or run.status != "RESERVED":
            raise StaleState("pilot_run_not_reserved")
        project = session.get(Project, run.project_id)
        if project is None:
            raise StaleState("pilot_project_missing")

        if project.revision != snapshot["project"]["revision"]:
            run.status = "FAILED"
            run.finished_at = now()
            run.error_code = "PROJECT_CHANGED_DURING_PILOT"
            run.output = {
                "pilot_record_only": True,
                "result": result.value.model_dump(),
                "retrieved_sources": result.sources,
                "stale": True,
            }
            run.input_tokens = result.input_tokens
            run.output_tokens = result.output_tokens
            run.request_id = result.request_id
            log(
                session,
                "PILOT_EVALUATION_REJECTED_STALE",
                {"run_id": run.id, "record_only": True},
                project.id,
                "PILOT",
            )
            raise StaleState("trial_project_changed_during_evaluation")

        run.status = "SUCCEEDED"
        run.finished_at = now()
        run.output = {
            "pilot_record_only": True,
            "result": result.value.model_dump(),
            "retrieved_sources": result.sources,
            "applied_to_project": False,
        }
        run.input_tokens = result.input_tokens
        run.output_tokens = result.output_tokens
        run.request_id = result.request_id
        log(
            session,
            "PILOT_EVALUATION_RECORDED",
            {
                "run_id": run.id,
                "recommendation": result.value.recommendation,
                "project_revision": project.revision,
                "applied_to_project": False,
            },
            project.id,
            "PILOT",
        )


def fail_pilot(store: Store, run_id: str, exc: Exception) -> None:
    """Close a reserved pilot run without retrying it automatically."""
    uncertain = isinstance(exc, ProviderError) and exc.uncertain
    code = exc.code if isinstance(exc, ProviderError) else type(exc).__name__
    with store.tx() as session:
        run = session.get(Run, run_id)
        if run is None or run.status != "RESERVED":
            return
        run.status = "UNCERTAIN" if uncertain else "FAILED"
        run.finished_at = now()
        run.error_code = code[:200]
        log(
            session,
            "PILOT_EVALUATION_FAILED",
            {"run_id": run.id, "error_code": code[:200], "uncertain": uncertain},
            run.project_id,
            "PILOT",
        )
