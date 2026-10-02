"""Evidence-aware review pilot that may create a pending owner approval.

The model recommendation is never applied here. Only REVIEW run/audit records and,
when warranted, one PENDING approval are persisted.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from .approvals import create_approval
from .db import Store, log
from .domain import BudgetExhausted, Evaluation, Policy, RuleError, StaleState, digest
from .models import Approval, Project, Run, now
from .provider import ProviderError, Result


APPROVAL_ACTIONS = {"HUMAN_ACTIVE", "ADVANCE", "PARK", "ARCHIVE"}


def reserve_review_pilot(
    store: Store, slug: str, snapshot: dict, policy: Policy, model: str
) -> str:
    with store.tx() as session:
        project = session.scalar(select(Project).where(Project.slug == slug))
        if project is None:
            raise RuleError("trial_project_not_found")
        if project.mode == "ARCHIVED":
            raise RuleError("trial_project_archived")
        if project.revision != snapshot["project"]["revision"]:
            raise StaleState("trial_project_changed_before_review")
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
            output={"approval_review_pilot": True},
        )
        session.add(run)
        session.flush()
        log(
            session,
            "PILOT_APPROVAL_REVIEW_RESERVED",
            {
                "run_id": run.id,
                "project_revision": project.revision,
                "evidence_count": len(snapshot.get("evidence", [])),
            },
            project.id,
            "OWNER",
        )
        return run.id


def complete_review_pilot(
    store: Store,
    run_id: str,
    snapshot: dict,
    result: Result,
    policy: Policy,
) -> dict:
    evaluation = result.value
    if not isinstance(evaluation, Evaluation):
        raise RuleError("unexpected_evaluation_output")

    known_evidence = {item["id"] for item in snapshot.get("evidence", [])}
    if not set(evaluation.evidence_ids).issubset(known_evidence):
        raise RuleError("model_cited_unknown_evidence")

    stale = False
    outcome: dict = {}
    with store.tx() as session:
        run = session.get(Run, run_id)
        if run is None or run.status != "RESERVED":
            raise StaleState("approval_review_run_not_reserved")
        project = session.get(Project, run.project_id)
        if project is None:
            raise StaleState("approval_review_project_missing")

        if project.revision != snapshot["project"]["revision"]:
            run.status = "FAILED"
            run.finished_at = now()
            run.error_code = "PROJECT_CHANGED_DURING_APPROVAL_REVIEW"
            run.output = {
                "approval_review_pilot": True,
                "result": evaluation.model_dump(),
                "retrieved_sources": result.sources,
                "applied_to_project": False,
                "stale": True,
            }
            run.input_tokens = result.input_tokens
            run.output_tokens = result.output_tokens
            run.request_id = result.request_id
            log(
                session,
                "PILOT_APPROVAL_REVIEW_REJECTED_STALE",
                {"run_id": run.id},
                project.id,
                "PILOT",
            )
            stale = True
        else:
            recommendation = evaluation.recommendation
            effective_recommendation = recommendation
            approval = None
            approval_needed = recommendation in APPROVAL_ACTIONS

            # Do not manufacture redundant approvals.
            if recommendation == "PARK" and project.mode == "PARKED":
                approval_needed = False
                effective_recommendation = "HOLD"
            elif recommendation == "HUMAN_ACTIVE" and project.mode == "HUMAN":
                approval_needed = False
                effective_recommendation = "HOLD"

            if approval_needed:
                approval = create_approval(session, project, evaluation, policy)

            run.status = "SUCCEEDED"
            run.finished_at = now()
            run.output = {
                "approval_review_pilot": True,
                "result": evaluation.model_dump(),
                "retrieved_sources": result.sources,
                "applied_to_project": False,
                "approval_id": approval.id if approval else None,
                "approval_created": approval is not None,
                "effective_recommendation": effective_recommendation,
            }
            run.input_tokens = result.input_tokens
            run.output_tokens = result.output_tokens
            run.request_id = result.request_id

            log(
                session,
                "PILOT_APPROVAL_REVIEW_RECORDED",
                {
                    "run_id": run.id,
                    "recommendation": recommendation,
                    "effective_recommendation": effective_recommendation,
                    "approval_created": approval is not None,
                    "approval_id": approval.id if approval else None,
                    "applied_to_project": False,
                },
                project.id,
                "PILOT",
            )
            outcome = {
                "recommendation": recommendation,
                "effective_recommendation": effective_recommendation,
                "approval_created": approval is not None,
                "approval_action": approval.action if approval else None,
            }

    if stale:
        raise StaleState("trial_project_changed_during_approval_review")
    return outcome


def fail_review_pilot(store: Store, run_id: str, exc: Exception) -> None:
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
            "PILOT_APPROVAL_REVIEW_FAILED",
            {"run_id": run.id, "error_code": code[:200], "uncertain": uncertain},
            run.project_id,
            "PILOT",
        )
