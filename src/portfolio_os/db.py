from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session
from .domain import Busy, BudgetExhausted, Policy, ProjectInput, RuleError, StaleState, digest, public_url
from .models import Approval, Base, Control, Decision, Evidence, Project, Run, Task, now, uid

LOCK_ID = 76301001

def row_dict(obj) -> dict:
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}

def log(s: Session, event_name: str, data: dict, project_id: str | None = None, actor: str = "SYSTEM") -> None:
    s.add(Decision(project_id=project_id, actor=actor, event=event_name, data=data))

class Store:
    """Single-owner store. External calls must happen outside locked transactions."""
    def __init__(self, url: str, *, demo: bool = False):
        self.demo = demo
        if demo and not url.startswith("sqlite:"):
            raise RuleError("Demo data must never be written to production")
        if not demo and not url.startswith("postgresql+psycopg:"):
            raise RuleError("Production requires PostgreSQL")
        kwargs = {} if demo else {"connect_args": {"connect_timeout": 10, "prepare_threshold": None}, "pool_size": 1, "max_overflow": 0}
        self.engine = create_engine(url, pool_pre_ping=True, **kwargs)
        if demo:
            @event.listens_for(self.engine, "connect")
            def sqlite_settings(dbapi_connection, _):
                dbapi_connection.execute("PRAGMA foreign_keys=ON")
                dbapi_connection.execute("PRAGMA busy_timeout=30000")
        else:
            self.engine = self.engine.execution_options(schema_translate_map={None: "portfolio_os"})

    def initialize_demo(self) -> None:
        if not self.demo:
            raise RuleError("Use the reviewed Supabase migration, not create_all, in production")
        Base.metadata.create_all(self.engine)
        with self.tx() as s:
            if s.get(Control, 1) is None:
                s.add(Control(id=1, paused=True, lease_until=0))

    @contextmanager
    def tx(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as s:
            try:
                if self.demo:
                    s.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    s.execute(text("SET LOCAL lock_timeout = '5s'"))
                    s.execute(text("SET LOCAL statement_timeout = '15s'"))
                    s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_ID})
                yield s
                s.commit()
            except BaseException:
                s.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as s:
            yield s

    def project(self, slug: str) -> dict:
        with self.read() as s:
            p = s.scalar(select(Project).where(Project.slug == slug))
            if p is None:
                raise RuleError("Unknown project slug")
            return row_dict(p)

    def register(self, item: ProjectInput) -> str:
        with self.tx() as s:
            old = s.scalar(select(Project).where(Project.slug == item.slug))
            if old:
                return old.id
            p = Project(**item.model_dump())
            s.add(p)
            s.flush()
            log(s, "REGISTERED_UNTRIAGED", {"slug": p.slug, "source_note": p.source_note}, p.id, "OWNER")
            return p.id

    def enroll(self, slug: str) -> None:
        with self.tx() as s:
            p = s.scalar(select(Project).where(Project.slug == slug))
            if not p:
                raise RuleError("Unknown project")
            if p.mode == "ARCHIVED":
                raise RuleError("Archived projects require a separate reviewed restoration")
            p.automation_enabled = True
            if p.stage == "INBOX":
                p.stage = "IDEA"
            if p.mode == "PARKED":
                p.mode = "AI"
            p.revision += 1
            p.next_review_at = now()
            p.idle_reviews = 0
            p.updated_at = now()
            self._invalidate_work(s, p.id)
            log(s, "ENROLLED", {"revision": p.revision}, p.id, "OWNER")

    def update_brief(self, slug: str, summary: str) -> None:
        if not 1 <= len(summary) <= 4000:
            raise RuleError("Brief must be 1–4000 characters")
        with self.tx() as s:
            p = s.scalar(select(Project).where(Project.slug == slug))
            if not p:
                raise RuleError("Unknown project")
            p.summary = summary
            p.revision += 1
            p.updated_at = now()
            p.next_review_at = now()
            p.idle_reviews = 0
            self._invalidate_work(s, p.id)
            log(s, "BRIEF_UPDATED", {"revision": p.revision}, p.id, "OWNER")

    def _invalidate_work(self, s: Session, project_id: str) -> None:
        for a in s.scalars(select(Approval).where(Approval.project_id == project_id, Approval.status == "PENDING")):
            a.status = "STALE"
        for task in s.scalars(select(Task).where(Task.project_id == project_id, Task.status == "QUEUED")):
            task.status = "CANCELLED"
            task.error_code = "PROJECT_REVISION_CHANGED"

    def add_observation(self, slug: str, statement: str, *, customer_signal: bool = False, url: str | None = None) -> str:
        if not 1 <= len(statement) <= 2000:
            raise RuleError("Observation must be 1–2000 characters")
        url = public_url(url) if url else None
        with self.tx() as s:
            p = s.scalar(select(Project).where(Project.slug == slug))
            if not p:
                raise RuleError("Unknown project")
            fp = digest({"observation": statement, "url": url})
            old = s.scalar(select(Evidence).where(Evidence.project_id == p.id, Evidence.fingerprint == fp))
            if old:
                return old.id
            e = Evidence(project_id=p.id, statement=statement, title="Owner observation", url=url,
                         kind="HUMAN_OBSERVATION", customer_signal=customer_signal,
                         limitation="Owner-supplied observation; not independently audited", fingerprint=fp)
            s.add(e)
            s.flush()
            p.revision += 1
            p.updated_at = now()
            p.next_review_at = now()
            p.idle_reviews = 0
            self._invalidate_work(s, p.id)
            log(s, "EVIDENCE_ADDED", {"evidence_id": e.id, "customer_signal": customer_signal}, p.id, "OWNER")
            return e.id

    def pause(self, paused: bool) -> None:
        with self.tx() as s:
            c = s.get(Control, 1)
            if c is None:
                raise RuleError("Database is not initialized")
            c.paused = paused
            log(s, "PAUSED" if paused else "RESUMED", {}, actor="OWNER")

    def acquire(self, policy: Policy) -> str:
        with self.tx() as s:
            c = s.get(Control, 1)
            if not c or c.paused:
                raise RuleError("System is paused. No paid call was started.")
            if c.lease_token and c.lease_until > now():
                raise Busy("Another tick holds the lease")
            if c.lease_token:
                raise Busy("Expired lease found. Run recover --confirm before restarting")
            token = uid()
            c.lease_token = token
            c.lease_until = now() + policy.lease_seconds
            return token

    def fence(self, s: Session, token: str) -> None:
        c = s.get(Control, 1)
        if not c or c.lease_token != token or c.lease_until <= now():
            raise StaleState("Worker lease lost or expired")
        if c.paused:
            raise RuleError("System paused while work was in progress; result not applied")

    def release(self, token: str) -> None:
        with self.tx() as s:
            c = s.get(Control, 1)
            if c and c.lease_token == token:
                c.lease_token = None
                c.lease_until = 0

    def recover(self) -> dict:
        with self.tx() as s:
            c = s.get(Control, 1)
            if c and c.lease_token and c.lease_until > now():
                raise Busy("Lease has not expired; do not recover a running worker")
            runs = list(s.scalars(select(Run).where(Run.status == "RESERVED")))
            tasks = list(s.scalars(select(Task).where(Task.status == "RUNNING")))
            for run in runs:
                run.status = "UNCERTAIN"
                run.error_code = "WORKER_INTERRUPTED"
            for task in tasks:
                task.status = "UNCERTAIN"
                task.error_code = "WORKER_INTERRUPTED"
            if c:
                c.lease_token = None
                c.lease_until = 0
            result = {"uncertain_runs": len(runs), "uncertain_tasks": len(tasks)}
            log(s, "RECOVERED_WITHOUT_RETRY", result, actor="OWNER")
            return result

    def expire_approvals(self) -> None:
        with self.tx() as s:
            for a in s.scalars(select(Approval).where(Approval.status == "PENDING", Approval.expires_at <= now())):
                a.status = "EXPIRED"

    def due(self, limit: int) -> list[str]:
        with self.read() as s:
            projects = s.scalars(select(Project).where(Project.automation_enabled.is_(True), Project.stage != "INBOX",
                Project.mode != "ARCHIVED", Project.next_review_at <= now()).order_by(Project.next_review_at, Project.created_at, Project.slug))
            result = []
            for p in projects:
                blocked = s.scalar(select(func.count()).select_from(Task).where(Task.project_id == p.id, Task.status.in_(["QUEUED", "RUNNING", "UNCERTAIN"])))
                blocked += s.scalar(select(func.count()).select_from(Approval).where(Approval.project_id == p.id, Approval.status == "PENDING"))
                blocked += s.scalar(select(func.count()).select_from(Run).where(Run.project_id == p.id, Run.status.in_(["RESERVED", "UNCERTAIN"])))
                if not blocked:
                    result.append(p.id)
                if len(result) == limit:
                    break
            return result

    def snapshot(self, project_id: str, policy: Policy) -> dict:
        with self.read() as s:
            p = s.get(Project, project_id)
            if not p:
                raise RuleError("Unknown project")
            evidence = s.scalars(select(Evidence).where(Evidence.project_id == project_id).order_by(Evidence.created_at.desc(), Evidence.id).limit(policy.max_evidence_items_in_context))
            return {"project": row_dict(p), "evidence": [row_dict(e) for e in evidence],
                    "human_active": s.scalar(select(func.count()).select_from(Project).where(Project.mode == "HUMAN")),
                    "human_limit": 3,
                    "pending_approvals": s.scalar(select(func.count()).select_from(Approval).where(Approval.status == "PENDING")),
                    "as_of_utc": datetime.now(timezone.utc).isoformat()}

    def reserve(self, project_id: str, kind: str, snapshot: dict, policy: Policy, token: str, model: str) -> str:
        with self.tx() as s:
            self.fence(s, token)
            day = datetime.now(timezone.utc).date().isoformat()
            provider = "DEMO" if self.demo else "OPENAI"
            used = s.scalar(select(func.count()).select_from(Run).where(Run.day == day, Run.provider == provider))
            if used >= policy.max_paid_requests_per_day:
                raise BudgetExhausted("Daily request reservation limit reached (UTC day)")
            p = s.get(Project, project_id)
            if not p or p.revision != snapshot["project"]["revision"] or not p.automation_enabled:
                raise StaleState("Project changed before request")
            r = Run(project_id=project_id, kind=kind, provider=provider, model=model, day=day, input_hash=digest(snapshot), policy_hash=digest(policy.model_dump()))
            s.add(r)
            s.flush()
            return r.id

    def fail_run(self, run_id: str, code: str, *, uncertain: bool = False) -> None:
        with self.tx() as s:
            run = s.get(Run, run_id)
            if run and run.status == "RESERVED":
                run.status = "UNCERTAIN" if uncertain else "FAILED"
                run.finished_at = now()
                run.error_code = code[:200]
                p = s.get(Project, run.project_id)
                p.next_review_at = max(p.next_review_at, now() + 86400)

    def resolve_uncertain(self, run_id: str, note: str) -> None:
        with self.tx() as s:
            r = s.get(Run, run_id)
            if not r or r.status != "UNCERTAIN":
                raise RuleError("Run is not UNCERTAIN")
            r.status = "ABANDONED"
            r.finished_at = now()
            log(s, "UNCERTAIN_RUN_ACKNOWLEDGED", {"run_id": run_id, "note": note}, r.project_id, "OWNER")

    def retry_task(self, task_id: str) -> None:
        with self.tx() as s:
            t = s.get(Task, task_id)
            if not t or t.status not in ("FAILED", "UNCERTAIN"):
                raise RuleError("Only failed/uncertain tasks can be explicitly retried")
            p = s.get(Project, t.project_id)
            if p.revision != t.project_revision or p.mode == "ARCHIVED":
                raise StaleState("Project changed; create a fresh task instead")
            if t.run_id:
                r = s.get(Run, t.run_id)
                if r and r.status == "UNCERTAIN":
                    raise RuleError("Acknowledge the uncertain run before retrying")
            t.status = "QUEUED"
            t.started_at = None
            t.finished_at = None
            t.run_id = None
            log(s, "TASK_RETRY_AUTHORIZED", {"task_id": t.id}, t.project_id, "OWNER")

    def export(self) -> dict:
        with self.read() as s:
            tables = {"projects": Project, "evidence": Evidence, "tasks": Task, "approvals": Approval, "runs": Run, "decisions": Decision}
            result = {key: [row_dict(o) for o in s.scalars(select(model))] for key, model in tables.items()}
            c = s.get(Control, 1)
            result.update({"demo": self.demo, "paused": c.paused if c else True, "generated_at": now(), "human_limit": 3})
            return result
