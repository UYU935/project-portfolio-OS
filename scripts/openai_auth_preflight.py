"""Manual OpenAI API authentication/model-visibility preflight. No inference call."""
from __future__ import annotations
import json
import os
import sys
import httpx

DEFAULT_MODEL = "gpt-6-sol"

def emit(status: str, reason: str, *, model_accessible: bool | None = None) -> int:
    payload = {"status": status, "reason": reason, "inference_requests_made": 0}
    if model_accessible is not None:
        payload["model_accessible"] = bool(model_accessible)
    print(json.dumps(payload, sort_keys=True))
    return 0 if status == "ready" else 2

def check(key: str, model: str, *, transport=None) -> int:
    if not key:
        return emit("blocked", "missing_openai_api_key")
    if not model:
        return emit("blocked", "missing_openai_model")
    try:
        with httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"Authorization": f"Bearer {key}"},
        ) as client:
            response = client.get("https://api.openai.com/v1/models")
    except (httpx.TimeoutException, httpx.NetworkError):
        return emit("blocked", "openai_network_error")
    if response.status_code == 401:
        return emit("blocked", "openai_authentication_failed")
    if response.status_code == 403:
        return emit("blocked", "openai_access_forbidden")
    if response.status_code == 429:
        return emit("blocked", "openai_rate_limited")
    if response.status_code >= 400:
        return emit("blocked", "openai_api_error")
    try:
        data = response.json()
        ids = {item.get("id") for item in data.get("data", []) if isinstance(item, dict)}
    except (ValueError, TypeError, AttributeError):
        return emit("blocked", "openai_invalid_response")
    accessible = model in ids
    return emit("ready" if accessible else "blocked",
                "openai_auth_and_model_access_ok" if accessible else "openai_model_not_listed",
                model_accessible=accessible)

def main() -> int:
    if os.environ.get("OPENAI_PREFLIGHT_REQUESTED") != "1":
        return emit("blocked", "manual_acknowledgment_required")
    return check(os.environ.get("OPENAI_API_KEY", ""),
                 os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))

if __name__ == "__main__":
    raise SystemExit(main())
