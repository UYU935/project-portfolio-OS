from __future__ import annotations
from pathlib import Path
from sqlalchemy import func, select
from .approvals import create_approval
from .db import Store, log, row_dict
from .domain import BudgetExhausted, Evaluation, Policy, Research, RuleError, StaleState, digest
from .models import Approval, Evidence, Project, Run, Task, now
from .provider import Provider, ProviderError, Result
from .workspace import codex_brief

def finish_run(run: Run, result: Result) -> None:
    run.status = "SUCCEEDED"
    run.finished_at = now()
    run.output = {"result": result.value.model_dump(), "retrieved_sources": result.sources}
    run.input_tokens = result.input_tokens
    run.output_tokens = result.output_tokens
    run.request_id = result.request_id

def apply_evaluation(store: Store, token: str, snapshot: dict, run_id: str, result: Result, policy: Policy) -> str:
    ev = result.value
    if not isinstance(ev, Evaluation):
        raise RuleError("Unexpected model output type")
    known = {e["id"] for e in snapshot["evidence"]}
    if not set(ev.evidence_ids).issubset(known):
        raise RuleError("Model cited evidence IDs that were not provided")
    if ev.recommendation != "ADVANCE" and ev.target_stage is not None:
        raise RuleError("Unexpected target stage")
    with store.tx() as s:
        store.fence(s, token)
        p = s.get(Project, snapshot["project"]["id"])
        if p.revision != snapshot["project"]["revision"]:
            raise StaleState("Project changed during model evaluation")
        run = s.get(Run, run_id)
        if not run or run.status != "RESERVED":
            raise StaleState("Run already finalized")
        action = ev.recommendation
        p.last_review_at = now()
        p.next_review_at = now() + max(policy.min_review_interval_days, ev.review_in_days) * 86400
        if p.mode == "PARKED":
            p.next_review_at = now() + policy.park_review_interval_days * 86400
        if action == "HUMAN_ACTIVE" and p.mode == "HUMAN":
            action = "HOLD"
        if action == "PARK" and p.mode == "AI" and policy.auto_park_ai_only:
            p.mode = "PARKED"
            p.revision += 1
            p.updated_at = now()
            p.next_review_at = now() + policy.park_review_interval_days * 86400
            store._invalidate_work(s, p.id)
            log(s, "AI_PARKED_REVERSIBLY", {"reason": ev.reason, "review_condition": ev.next_step}, p.id, "MANAGER")
        elif action == "PARK" and p.mode == "PARKED":
            action = "HOLD"
        elif action in ("PARK", "HUMAN_ACTIVE", "ADVANCE", "ARCHIVE"):
            create_approval(s, p, ev, policy)
        elif action in ("RESEARCH", "CODEX_BRIEF"):
            if action == "RESEARCH" and not policy.allow_web_research:
                action = "HOLD"
            elif p.mode == "PARKED" and action == "CODEX_BRIEF":
                action = "HOLD"
            elif p.idle_reviews >= policy.max_idle_reviews_before_park and p.mode == "AI":
                p.mode = "PARKED"
                p.revision += 1
                p.updated_at = now()
                p.next_review_at = now() + policy.park_review_interval_days * 86400
                store._invalidate_work(s, p.id)
                log(s, "NO_NEW_EVIDENCE_PARK", {"completed_empty_research": p.idle_reviews}, p.id)
                action = "PARK"
            else:
                key = digest({"p": p.id, "rev": p.revision, "kind": action, "due": snapshot["project"]["next_review_at"] if action == "RESEARCH" else 0})
                existing = s.scalar(select(Task).where(Task.dedupe_key == key))
                opened = s.scalar(select(func.count()).select_from(Task).where(Task.project_id == p.id, Task.status.in_(["QUEUED", "RUNNING", "UNCERTAIN"])))
                if not existing and not opened:
                    s.add(Task(project_id=p.id, project_revision=p.revision, kind=action, objective=ev.next_step, success_condition=ev.success_condition, dedupe_key=key))
        elif action != "HOLD":
            raise RuleError("Unrecognized action")
        finish_run(run, result)
        log(s, "REVIEW_COMPLETED", {"run_id": run_id, "action": action, "reason": ev.reason, "objection": ev.strongest_objection}, p.id, "MANAGER")
        return action

def claim_task(store: Store, token: str) -> dict | None:
    with store.tx() as s:
        store.fence(s, token)
        for t in s.scalars(select(Task).where(Task.status == "QUEUED").order_by(Task.created_at, Task.id)):
            p = s.get(Project, t.project_id)
            if not p.automation_enabled or p.mode == "ARCHIVED" or p.revision != t.project_revision:
                t.status = "CANCELLED"
                t.error_code = "PROJECT_NOT_ELIGIBLE"
                continue
            if p.mode == "PARKED" and t.kind == "CODEX_BRIEF":
                t.status = "CANCELLED"
                continue
            t.status = "RUNNING"
            t.started_at = now()
            return row_dict(t)
        return None

def persist_research(store: Store, token: str, task_id: str, snapshot: dict, run_id: str, result: Result, policy: Policy) -> int:
    research = result.value
    if not isinstance(research, Research):
        raise RuleError("Unexpected research output")
    if not store.demo and any(f.url not in result.sources for f in research.findings):
        raise RuleError("Unsupported evidence provenance")
    with store.tx() as s:
        store.fence(s, token)
        t = s.get(Task, task_id)
        p = s.get(Project, t.project_id)
        if p.revision != snapshot["project"]["revision"] or t.status != "RUNNING":
            raise StaleState("Task or project changed during research")
        added = 0
        for finding in research.findings:
            fp = digest({"url": finding.url})
            old = s.scalar(select(Evidence).where(Evidence.project_id == p.id, Evidence.fingerprint == fp))
            if old:
                continue
            s.add(Evidence(project_id=p.id, statement=finding.statement, url=finding.url, title=finding.title,
                limitation=finding.limitation, kind="DEMO" if store.demo else "WEB_RETRIEVED",
                customer_signal=False, run_id=run_id, fingerprint=fp))
            s.flush()
            added += 1
        t.status = "DONE"
        t.finished_at = now()
        t.run_id = run_id
        finish_run(s.get(Run, run_id), result)
        if added:
            p.revision += 1
            p.updated_at = now()
            p.idle_reviews = 0
            store._invalidate_work(s, p.id)
            p.next_review_at = now()
        else:
            p.idle_reviews += 1
            p.next_review_at = now() + policy.min_review_interval_days * 86400
        log(s, "RESEARCH_COMPLETED", {"task_id": t.id, "new_source_count": added, "note": "Sources are retrieved, NOT verified customer demand"}, p.id, "RESEARCH_WORKER")
        return added

async def work_one(store: Store, token: str, provider: Provider, root: Path, policy: Policy) -> str | None:
    task = claim_task(store, token)
    if not task:
        return None
    snapshot = store.snapshot(task["project_id"], policy)
    run_id = None
    try:
        if task["kind"] == "CODEX_BRIEF":
            artifact = codex_brief(root, snapshot, task)
            with store.tx() as s:
                store.fence(s, token)
                p = s.get(Project, task["project_id"])
                t = s.get(Task, task["id"])
                if p.revision != task["project_revision"] or t.status != "RUNNING":
                    raise StaleState("Project changed while creating brief")
                t.status = "DONE"
                t.finished_at = now()
                t.artifact = artifact
                log(s, "CODEX_BRIEF_CREATED_NOT_EXECUTED", {"task_id": t.id, "artifact": artifact}, p.id)
            return "CODEX_BRIEF_CREATED"
        if task["kind"] != "RESEARCH":
            raise RuleError("Task kind is not executable in phase 1")
        run_id = store.reserve(task["project_id"], "RESEARCH", snapshot, policy, token, provider.model)
        with store.tx() as s:
            store.fence(s, token)
            s.get(Task, task["id"]).run_id = run_id
        result = await provider.research(snapshot, task, policy)
        count = persist_research(store, token, task["id"], snapshot, run_id, result, policy)
        return f"RESEARCH_DONE:new_sources={count}"
    except BudgetExhausted:
        with store.tx() as s:
            t = s.get(Task, task["id"])
            if t.status == "RUNNING":
                t.status = "QUEUED"
                t.started_at = None
        return "BUDGET_LIMIT"
    except Exception as exc:
        uncertain = isinstance(exc, ProviderError) and exc.uncertain
        code = exc.code if isinstance(exc, ProviderError) else type(exc).__name__
        if run_id:
            store.fail_run(run_id, code, uncertain=uncertain)
        with store.tx() as s:
            t = s.get(Task, task["id"])
            if t.status == "RUNNING":
                t.status = "UNCERTAIN" if uncertain else "FAILED"
                t.error_code = code
                t.finished_at = now()
                log(s, "TASK_FAILED", {"task_id": t.id, "code": code}, t.project_id)
        return f"TASK_FAILED:{code}"

async def tick(store: Store, provider: Provider, root: Path, policy: Policy) -> dict:
    """One bounded iteration, not a persistent background service."""
    store.expire_approvals()
    token = store.acquire(policy)
    result = {"reviews": [], "tasks": [], "demo": store.demo}
    try:
        with store.read() as s:
            pending = s.scalar(select(func.count()).select_from(Approval).where(Approval.status == "PENDING"))
        due = [] if pending >= policy.max_pending_approvals else store.due(policy.max_reviews_per_tick)
        for project_id in due:
            run_id = None
            snapshot = store.snapshot(project_id, policy)
            try:
                run_id = store.reserve(project_id, "REVIEW", snapshot, policy, token, provider.model)
                output = await provider.evaluate(snapshot, policy)
                action = apply_evaluation(store, token, snapshot, run_id, output, policy)
                result["reviews"].append({"project": snapshot["project"]["slug"], "action": action})
            except BudgetExhausted:
                result["budget_limited"] = True
                break
            except Exception as exc:
                code = exc.code if isinstance(exc, ProviderError) else type(exc).__name__
                if run_id:
                    store.fail_run(run_id, code, uncertain=isinstance(exc, ProviderError) and exc.uncertain)
                result["reviews"].append({"project": snapshot["project"]["slug"], "error": code})
                if isinstance(exc, StaleState):
                    break
        for _ in range(policy.max_tasks_per_tick):
            outcome = await work_one(store, token, provider, root, policy)
            if outcome:
                result["tasks"].append(outcome)
            if not outcome or outcome == "BUDGET_LIMIT":
                break
        if pending >= policy.max_pending_approvals:
            result["approval_backpressure"] = True
        return result
    finally:
        store.release(token)
