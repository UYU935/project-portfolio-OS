from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
import httpx
from pydantic import BaseModel, ValidationError
from .domain import Evaluation, Policy, Research, RuleError, canonical, public_url, strict_json_schema

class ProviderError(RuleError):
    def __init__(self, code: str, *, uncertain: bool = False):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain

@dataclass
class Result:
    value: Evaluation | Research
    input_tokens: int = 0
    output_tokens: int = 0
    request_id: str | None = None
    sources: list[str] = field(default_factory=list)

class Provider(Protocol):
    model: str
    async def evaluate(self, snapshot: dict, policy: Policy) -> Result: ...
    async def research(self, snapshot: dict, task: dict, policy: Policy) -> Result: ...

class OpenAIProvider:
    """One Responses API request per call, no automatic retries or arbitrary tools."""
    def __init__(self, key: str, model: str, root: Path, *, transport=None):
        if not key or not model:
            raise RuleError("OPENAI_API_KEY and OPENAI_MODEL must both be configured")
        self._key = key
        self.model = model
        self.root = root
        self.transport = transport

    async def evaluate(self, snapshot: dict, policy: Policy) -> Result:
        return await self._call(Evaluation, self.root / "prompts/manager.md", snapshot, policy, search=False)

    async def research(self, snapshot: dict, task: dict, policy: Policy) -> Result:
        return await self._call(Research, self.root / "prompts/research.md", {"snapshot": snapshot, "task": task}, policy, search=True)

    async def _call(self, schema: type[BaseModel], prompt_path: Path, context: dict, policy: Policy, *, search: bool) -> Result:
        data = canonical(context)
        instructions = prompt_path.read_text(encoding="utf-8")
        if len(data) + len(instructions) > policy.max_input_characters:
            raise ProviderError("INPUT_TOO_LARGE")
        body: dict[str, Any] = {
            "model": self.model, "instructions": instructions,
            "input": [{"role": "user", "content": data}], "store": False,
            "max_output_tokens": policy.max_output_tokens_per_request,
            "text": {"format": {"type": "json_schema", "name": schema.__name__, "strict": True, "schema": strict_json_schema(schema)}},
        }
        if search:
            if not policy.allow_web_research:
                raise ProviderError("WEB_RESEARCH_DISABLED")
            body["tools"] = [{"type": "web_search", "search_context_size": "low"}]
            body["max_tool_calls"] = 1
            body["include"] = ["web_search_call.action.sources"]
        async def request() -> httpx.Response:
            async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(policy.request_timeout_seconds, connect=10)) as client:
                return await client.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}, json=body)
        try:
            resp = await asyncio.wait_for(request(), timeout=policy.request_timeout_seconds)
        except (TimeoutError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderError("NETWORK_COMPLETION_UNKNOWN", uncertain=True) from exc
        if resp.status_code >= 400:
            code = f"OPENAI_HTTP_{resp.status_code}"
            if resp.status_code == 429:
                try:
                    payload = resp.json()
                    err = payload.get("error", {}) if isinstance(payload, dict) else {}
                    err_code = err.get("code") if isinstance(err, dict) else None
                    err_type = err.get("type") if isinstance(err, dict) else None
                except ValueError:
                    err_code = err_type = None
                quota_codes = {
                    "credit_balance_exhausted": "OPENAI_CREDIT_BALANCE_EXHAUSTED",
                    "organization_usage_limit_exceeded": "OPENAI_ORGANIZATION_USAGE_LIMIT_EXCEEDED",
                    "organization_spend_limit_exceeded": "OPENAI_ORGANIZATION_SPEND_LIMIT_EXCEEDED",
                    "project_spend_limit_exceeded": "OPENAI_PROJECT_SPEND_LIMIT_EXCEEDED",
                }
                if err_code in quota_codes:
                    code = quota_codes[err_code]
                elif err_type == "insufficient_quota":
                    code = "OPENAI_QUOTA_EXCEEDED"
                else:
                    code = "OPENAI_RATE_LIMITED"
            raise ProviderError(code, uncertain=resp.status_code >= 500)
        if len(resp.content) > 4_000_000:
            raise ProviderError("RESPONSE_TOO_LARGE")
        try:
            response = resp.json()
        except ValueError as exc:
            raise ProviderError("NON_JSON_RESPONSE") from exc
        if response.get("status") != "completed":
            raise ProviderError("MODEL_RESPONSE_NOT_COMPLETED")
        text_parts = []
        sources: set[str] = set()
        searched = False
        for item in response.get("output", []):
            if item.get("type") == "web_search_call":
                if item.get("status") == "completed":
                    searched = True
                for src in item.get("action", {}).get("sources", []):
                    try:
                        sources.add(public_url(src.get("url", "")))
                    except ValueError:
                        pass
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "refusal":
                        raise ProviderError("MODEL_REFUSAL")
                    if content.get("type") == "output_text":
                        text_parts.append(content.get("text", ""))
                        for ann in content.get("annotations", []):
                            if ann.get("type") == "url_citation":
                                try:
                                    sources.add(public_url(ann.get("url", "")))
                                except ValueError:
                                    pass
        try:
            value = schema.model_validate_json("".join(text_parts))
        except (ValueError, ValidationError) as exc:
            raise ProviderError("OUTPUT_SCHEMA_INVALID") from exc
        if search:
            if not searched:
                raise ProviderError("NO_WEB_SEARCH_PERFORMED")
            assert isinstance(value, Research)
            if any(f.url not in sources for f in value.findings):
                raise ProviderError("SOURCE_NOT_IN_RETRIEVED_RESULTS")
        usage = response.get("usage") or {}
        return Result(value=value, input_tokens=int(usage.get("input_tokens", 0)), output_tokens=int(usage.get("output_tokens", 0)), request_id=resp.headers.get("x-request-id") or response.get("id"), sources=sorted(sources))

class DemoProvider:
    """Deterministic fixture, not AI research."""
    model = "DEMO-NO-API"
    async def evaluate(self, snapshot: dict, policy: Policy) -> Result:
        project = snapshot["project"]
        action = "CODEX_BRIEF" if project["domain"] == "software" else "RESEARCH"
        if snapshot["evidence"]:
            action = "HUMAN_ACTIVE"
        value = Evaluation(recommendation=action,
            reason="【DEMO】動作検証用の固定応答。市場性を評価した結果ではありません。",
            strongest_objection="【DEMO】顧客の利用・支払意思を検証していません。",
            uncertainties=["【DEMO】対象顧客と支払意思は未確認。"],
            evidence_ids=[e["id"] for e in snapshot["evidence"][:1]],
            next_step="【DEMO】仮説に対する最小の確認手順を文書化する。",
            success_condition="【DEMO】未検証の仮説と必要な外部観測を区別できる。",
            target_stage=None, review_in_days=7)
        return Result(value)
    async def research(self, snapshot: dict, task: dict, policy: Policy) -> Result:
        return Result(Research(summary="【DEMO】検索を実行していません。費用も発生しません。", findings=[], counterargument="【DEMO】これはタスク遷移のテストであり調査成果ではありません。", unanswered=["実際の市場・競合情報は本番接続後に収集。 "]))
