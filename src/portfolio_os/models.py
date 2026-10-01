from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> int:
    return int(time.time())


def uid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Control(Base):
    __tablename__ = "control"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("id = 1", name="singleton_control"),)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(20), default="general")
    source_note: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(20), default="INBOX")
    mode: Mapped[str] = mapped_column(String(20), default="PARKED")
    human_slot: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    next_review_at: Mapped[int] = mapped_column(Integer, default=now)
    last_review_at: Mapped[int] = mapped_column(Integer, default=0)
    idle_reviews: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    updated_at: Mapped[int] = mapped_column(Integer, default=now)
    __table_args__ = (
        CheckConstraint("stage in ('INBOX','IDEA','EXPLORE','VALIDATE','PILOT','EXECUTE')", name="stage_allowed"),
        CheckConstraint("mode in ('AI','HUMAN','PARKED','ARCHIVED')", name="mode_allowed"),
        CheckConstraint("human_slot is null or human_slot between 1 and 3", name="human_capacity"),
        CheckConstraint("(mode = 'HUMAN' and human_slot is not null) or (mode <> 'HUMAN' and human_slot is null)", name="human_slot_consistency"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("project_due_idx", "automation_enabled", "next_review_at"),
    )


class Run(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="RESERVED")
    day: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[int] = mapped_column(Integer, default=now)
    finished_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    policy_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    __table_args__ = (Index("run_day_idx", "day", "provider"),)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    statement: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    limitation: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(30))
    customer_signal: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    fingerprint: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint", name="evidence_dedupe"),
        CheckConstraint("kind in ('HUMAN_OBSERVATION','WEB_RETRIEVED','DEMO')", name="evidence_kind_allowed"),
        CheckConstraint("not customer_signal or kind = 'HUMAN_OBSERVATION'", name="customer_evidence_human_only"),
    )


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    project_revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    objective: Mapped[str] = mapped_column(Text)
    success_condition: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finished_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    artifact: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    __table_args__ = (CheckConstraint("kind in ('RESEARCH','CODEX_BRIEF')", name="task_allowlist"),)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    project_revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(24))
    target_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    objection: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    expires_at: Mapped[int] = mapped_column(Integer)
    decided_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (CheckConstraint("action in ('HUMAN_ACTIVE','ADVANCE','PARK','ARCHIVE')", name="approval_allowlist"),)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String(30))
    event: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
