from __future__ import annotations

import hashlib
import ipaddress
import json
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

STAGES = ("INBOX", "IDEA", "EXPLORE", "VALIDATE", "PILOT", "EXECUTE")
MODES = ("AI", "HUMAN", "PARKED", "ARCHIVED")

class RuleError(ValueError):
    """Recoverable validation failure; never expose credentials here."""

class BudgetExhausted(RuleError):
    pass

class StaleState(RuleError):
    pass

class Busy(RuleError):
    pass

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Evaluation(StrictModel):
    recommendation: Literal["HOLD", "RESEARCH", "CODEX_BRIEF", "PARK", "HUMAN_ACTIVE", "ADVANCE", "ARCHIVE"]
    reason: str = Field(min_length=1, max_length=1600)
    strongest_objection: str = Field(min_length=1, max_length=1000)
    uncertainties: list[str] = Field(max_length=6)
    evidence_ids: list[str] = Field(max_length=12)
    next_step: str = Field(min_length=1, max_length=1200)
    success_condition: str = Field(min_length=1, max_length=1000)
    target_stage: Literal["IDEA", "EXPLORE", "VALIDATE", "PILOT", "EXECUTE"] | None
    review_in_days: int = Field(ge=1, le=90)

class Finding(StrictModel):
    statement: str = Field(min_length=1, max_length=1200)
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    limitation: str = Field(min_length=1, max_length=700)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return public_url(value)

class Research(StrictModel):
    summary: str = Field(min_length=1, max_length=2000)
    findings: list[Finding] = Field(max_length=6)
    counterargument: str = Field(min_length=1, max_length=1200)
    unanswered: list[str] = Field(max_length=6)

class ProjectInput(StrictModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    name: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=4000)
    domain: Literal["general", "medical", "education", "software", "media"] = "general"
    source_note: str = Field(default="Owner input", max_length=800)

class Policy(StrictModel):
    human_wip_limit: Literal[3] = 3
    max_reviews_per_tick: int = Field(default=3, ge=1, le=10)
    max_tasks_per_tick: int = Field(default=1, ge=0, le=3)
    max_paid_requests_per_day: int = Field(default=6, ge=1, le=100)
    max_output_tokens_per_request: int = Field(default=2400, ge=512, le=12000)
    max_input_characters: int = Field(default=18000, ge=2000, le=100000)
    request_timeout_seconds: int = Field(default=90, ge=10, le=180)
    lease_seconds: int = Field(default=900, ge=300, le=3600)
    approval_ttl_days: int = Field(default=7, ge=1, le=30)
    max_pending_approvals: int = Field(default=5, ge=1, le=20)
    min_review_interval_days: int = Field(default=7, ge=1, le=90)
    park_review_interval_days: int = Field(default=30, ge=7, le=180)
    max_idle_reviews_before_park: int = Field(default=2, ge=2, le=6)
    max_evidence_items_in_context: int = Field(default=12, ge=1, le=40)
    allow_web_research: bool = True
    auto_park_ai_only: bool = True
    allow_codex_execution: Literal[False] = False

def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

def public_url(value: str) -> str:
    """Validate provenance links without fetching URLs or verifying claims."""
    if any(c in value for c in "\r\n\t"):
        raise ValueError("URL contains control characters")
    p = urlsplit(value)
    host = p.hostname or ""
    if p.scheme not in ("https", "http") or not host or p.username or p.password:
        raise ValueError("A public HTTP(S) URL without embedded credentials is required")
    if host.lower() in ("localhost", "localhost.localdomain") or host.endswith((".local", ".internal")):
        raise ValueError("Private URLs are not allowed")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("Private network addresses are not allowed")
    except ValueError as exc:
        if "Private network" in str(exc):
            raise
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))

def strict_json_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for key in ("minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems", "pattern"):
                node.pop(key, None)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(schema)
    return schema
