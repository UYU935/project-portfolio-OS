from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .domain import Policy, RuleError


def read_dotenv(path: Path) -> None:
    """Literal-only .env reader: no shell expansion or executable interpolation."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.replace("_", "").isalnum():
            raise RuleError("Invalid .env entry; use KEY=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    root: Path
    database_url: str = field(repr=False)
    model: str = ""
    api_key: str = field(default="", repr=False)
    demo: bool = False

    @classmethod
    def load(cls) -> "Config":
        read_dotenv(Path.cwd() / ".env")
        root = Path(os.environ.get("PORTFOLIO_WORKSPACE", ".")).resolve()
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuleError("DATABASE_URL is missing. For a key-free test run: portfolio-os demo")
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
        if not url.startswith("postgresql+psycopg://"):
            raise RuleError("Live mode requires Supabase/PostgreSQL. SQLite is restricted to demo/tests.")
        parsed = urlsplit(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if params.get("sslmode") != ["verify-full"]:
            raise RuleError("Live connection requires sslmode=verify-full (and a trusted CA if necessary)")
        if not parsed.hostname or any(k in params for k in ("host", "hostaddr", "service", "user", "password", "dbname", "port", "options")):
            raise RuleError("Use one explicit database host; query host/service overrides are not allowed")
        return cls(root, url, os.environ.get("OPENAI_MODEL", ""), os.environ.get("OPENAI_API_KEY", ""))

    def policy(self) -> Policy:
        path = self.root / "config" / "policy.json"
        if not path.exists():
            raise RuleError("config/policy.json is missing; run from the extracted repository root")
        policy = Policy.model_validate_json(path.read_text(encoding="utf-8"))
        worst = (policy.max_reviews_per_tick + policy.max_tasks_per_tick) * (policy.request_timeout_seconds + 15)
        if policy.lease_seconds <= worst:
            raise RuleError("lease_seconds must exceed the entire bounded tick duration")
        return policy
