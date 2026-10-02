"""One-project research pilot.

Persists retrieved public-web evidence and its run/audit trail, while keeping
the portfolio paused and never changing project stage/mode/automation/human slot.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from .db import Store, log
from .domain import BudgetExhausted, Policy, Research, RuleError, StaleState, digest
from .models import Evidence, Project, Run, now
from .provider import ProviderError, Result


def reserve_research_pilot(
    store: Store, slug: str, snapshot: dict, policy: Policy, model: str
) -> str:
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
            kind="RESEARCH",
            provider="OPENAI",
            model=model,
            day=day,
            input_hash=digest(snapshot),
            policy_hash=digest(policy.model_dump()),
            output={"pilot_research_only": True},
        )
        session.add(run)
        session.flush()
        log(
            session,
            "PILOT_RESEARCH_RESERVED",
            {
                "run_id": run.id,
                "project_revision": project.revision,
                "research_only": True,
            },
            project.id,
            "OWNER",
        )
        return run.id


def complete_research_pilot(
    store: Store, run_id: str, snapshot: dict, result: Result
) -> int:
    research = result.value
    if not isinstance(research, Research):
        raise RuleError("unexpected_research_output")

    # Provider already enforces that every finding URL came from retrieved sources.
    # Re-check before persistence as a second deterministic boundary.
    if any(finding.url not in result.sources for finding in research.findings):
        raise RuleError("unsupported_evidence_provenance")

    with store.tx() as session:
        run = session.get(Run, run_id)
        if run is None or run.status != "RESERVED":
            raise StaleState("pilot_research_run_not_reserved")
        project = session.get(Project, run.project_id)
        if project is None:
            raise StaleState("pilot_research_project_missing")
        if project.revision != snapshot["project"]["revision"]:
            run.status = "FAILED"
            run.finished_at = now()
            run.error_code = "PROJECT_CHANGED_DURING_RESEARCH_PILOT"
            log(
                session,
                "PILOT_RESEARCH_REJECTED_STALE",
                {"run_id": run.id, "research_only": True},
                project.id,
                "PILOT",
            )
            raise StaleState("trial_project_changed_during_research")

        added = 0
        for finding in research.findings:
            fingerprint = digest({"url": finding.url})
            existing = session.scalar(
                select(Evidence).where(
                    Evidence.project_id == project.id,
                    Evidence.fingerprint == fingerprint,
                )
            )
            if existing:
                continue
            session.add(
                Evidence(
                    project_id=project.id,
                    statement=finding.statement,
                    url=finding.url,
                    title=finding.title,
                    limitation=finding.limitation,
                    kind="WEB_RETRIEVED",
                    customer_signal=False,
                    run_id=run.id,
                    fingerprint=fingerprint,
                )
            )
            added += 1

        run.status = "SUCCEEDED"
        run.finished_at = now()
        run.output = {
            "pilot_research_only": True,
            "result": research.model_dump(),
            "retrieved_sources": result.sources,
            "applied_to_project_stage_or_mode": False,
            "new_evidence_count": added,
        }
        run.input_tokens = result.input_tokens
        run.output_tokens = result.output_tokens
        run.request_id = result.request_id

        if added:
            project.revision += 1
            project.updated_at = now()

        log(
            session,
            "PILOT_RESEARCH_RECORDED",
            {
                "run_id": run.id,
                "new_evidence_count": added,
                "project_revision_after": project.revision,
                "stage_or_mode_changed": False,
                "note": "Retrieved public sources are evidence, not verified customer demand.",
            },
            project.id,
            "PILOT",
        )
        return added


def fail_research_pilot(store: Store, run_id: str, exc: Exception) -> None:
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
            "PILOT_RESEARCH_FAILED",
            {"run_id": run.id, "error_code": code[:200], "uncertain": uncertain},
            run.project_id,
            "PILOT",
        )
