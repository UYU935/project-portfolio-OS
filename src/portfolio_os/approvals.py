from __future__ import annotations
import hmac
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .db import Store, log
from .domain import Evaluation, Policy, RuleError, STAGES, StaleState, digest
from .models import Approval, Evidence, Project, now, uid

def approval_payload(a: Approval) -> dict:
    return {"id": a.id, "project_id": a.project_id, "project_revision": a.project_revision,
            "action": a.action, "target_stage": a.target_stage, "reason": a.reason,
            "objection": a.objection, "expires_at": a.expires_at}

def create_approval(s: Session, p: Project, ev: Evaluation, policy: Policy) -> Approval | None:
    if ev.recommendation not in ("HUMAN_ACTIVE", "ADVANCE", "PARK", "ARCHIVE"):
        raise RuleError("Action is not in the approval allowlist")
    if ev.recommendation == "ADVANCE":
        if ev.target_stage is None or STAGES.index(ev.target_stage) != STAGES.index(p.stage) + 1:
            raise RuleError("Stage transitions must advance exactly one stage")
    elif ev.target_stage is not None:
        raise RuleError("Only ADVANCE may contain a target stage")
    old = s.scalar(select(Approval).where(Approval.project_id == p.id, Approval.status == "PENDING"))
    if old:
        return old
    count = s.scalar(select(func.count()).select_from(Approval).where(Approval.status == "PENDING"))
    if count >= policy.max_pending_approvals:
        log(s, "APPROVAL_BACKPRESSURE", {"limit": policy.max_pending_approvals}, p.id)
        return None
    a = Approval(id=uid(), project_id=p.id, project_revision=p.revision, action=ev.recommendation,
                 target_stage=ev.target_stage, reason=ev.reason, objection=ev.strongest_objection,
                 expires_at=now() + policy.approval_ttl_days * 86400)
    a.digest = digest(approval_payload(a))
    s.add(a)
    log(s, "APPROVAL_REQUESTED", {"approval_id": a.id, "action": a.action, "revision": p.revision}, p.id, "MANAGER")
    return a

def decide(store: Store, approval_id: str, expected_digest: str, *, approve: bool, note: str, replace_slug: str | None = None) -> str:
    """Owner-only CLI operation; no public HTTP endpoint accepts this operation."""
    error = None
    result = ""
    with store.tx() as s:
        a = s.get(Approval, approval_id)
        if not a:
            raise RuleError("Unknown approval")
        if a.status != "PENDING":
            raise RuleError(f"Approval is not pending: {a.status}")
        computed = digest(approval_payload(a))
        if not hmac.compare_digest(expected_digest, a.digest) or not hmac.compare_digest(computed, a.digest):
            raise RuleError("Approval digest mismatch. Re-read the current approval before deciding.")
        p = s.get(Project, a.project_id)
        if a.expires_at <= now():
            a.status = "EXPIRED"
            error = "Approval expired; request a fresh review"
        elif p.revision != a.project_revision:
            a.status = "STALE"
            error = "Project changed since proposal; request a fresh review"
        elif not approve:
            a.status = "REJECTED"
            a.decided_at = now()
            a.decision_note = note
            p.next_review_at = now() + 7 * 86400
            result = a.status
            log(s, "APPROVAL_REJECTED", {"approval_id": a.id, "note": note}, p.id, "OWNER")
        else:
            if replace_slug and a.action != "HUMAN_ACTIVE":
                raise RuleError("Replacement is only valid for HUMAN_ACTIVE")
            if a.action == "HUMAN_ACTIVE":
                if p.mode != "HUMAN":
                    occupied = set(s.scalars(select(Project.human_slot).where(Project.human_slot.is_not(None))))
                    if replace_slug:
                        replacement = s.scalar(select(Project).where(Project.slug == replace_slug))
                        if not replacement or replacement.mode != "HUMAN" or replacement.id == p.id:
                            raise RuleError("Replacement must be a different HUMAN project")
                        freed_slot = replacement.human_slot
                        replacement.mode = "AI"
                        replacement.human_slot = None
                        replacement.revision += 1
                        replacement.updated_at = now()
                        store._invalidate_work(s, replacement.id)
                        log(s, "HUMAN_SLOT_RELEASED", {"replaced_by": p.id}, replacement.id, "OWNER")
                        s.flush()
                        occupied.discard(freed_slot)
                    free = sorted({1, 2, 3} - occupied)
                    if not free:
                        raise RuleError("HUMAN WIP LIMIT = 3. Explicitly choose a replacement project.")
                    p.mode = "HUMAN"
                    p.human_slot = free[0]
            elif a.action == "ADVANCE":
                target = a.target_stage
                if target not in STAGES or STAGES.index(target) != STAGES.index(p.stage) + 1:
                    raise RuleError("Invalid or non-adjacent stage transition")
                if target in ("PILOT", "EXECUTE"):
                    observed = s.scalar(select(func.count()).select_from(Evidence).where(Evidence.project_id == p.id, Evidence.customer_signal.is_(True), Evidence.kind == "HUMAN_OBSERVATION"))
                    if p.mode != "HUMAN" or not observed:
                        raise RuleError("PILOT/EXECUTE requires a HUMAN slot and owner-entered customer evidence")
                p.stage = target
            elif a.action in ("PARK", "ARCHIVE"):
                p.mode = "PARKED" if a.action == "PARK" else "ARCHIVED"
                p.human_slot = None
                if a.action == "ARCHIVE":
                    p.automation_enabled = False
            else:
                raise RuleError("Unsupported approval action")
            p.revision += 1
            p.updated_at = now()
            p.next_review_at = now() + (30 * 86400 if p.mode == "PARKED" else 7 * 86400)
            store._invalidate_work(s, p.id)
            a.status = "APPROVED"
            a.decided_at = now()
            a.decision_note = note
            result = a.status
            log(s, "APPROVAL_APPLIED", {"approval_id": a.id, "action": a.action, "new_revision": p.revision, "note": note, "replace_slug": replace_slug}, p.id, "OWNER")
        if error:
            log(s, "APPROVAL_INVALIDATED", {"approval_id": a.id, "status": a.status}, p.id)
    if error:
        raise StaleState(error)
    return result
