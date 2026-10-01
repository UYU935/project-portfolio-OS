"""Closed, non-sensitive error categories for connection diagnostics.

Exception text is inspected only in memory and is NEVER returned or logged.
A category is evidence about the failure, not proof that a password is wrong.
"""
from __future__ import annotations

ERROR_CODES = frozenset({
    "configuration_invalid", "tls_verification_required", "driver_missing",
    "tls_ca_missing", "tls_certificate_rejected", "authentication_failed",
    "pooler_identity_rejected", "login_disabled", "hostname_resolution_failed",
    "connection_timeout", "connection_unavailable", "database_permission_denied",
    "database_schema_missing", "database_error", "diagnostic_internal_error",
})


def classify_error(exc: Exception, *, configuration: bool = False) -> str:
    """Return a fixed token; never interpolate exception data into output."""
    inner = getattr(exc, "orig", None)
    if inner is None:
        inner = exc
    message = str(inner).casefold()
    state = getattr(inner, "sqlstate", None)
    if configuration:
        return "tls_verification_required" if "sslmode=verify-full" in message else "configuration_invalid"
    if isinstance(inner, ImportError):
        return "driver_missing"
    if state == "28P01" or "password authentication failed" in message:
        return "authentication_failed"
    if "tenant or user not found" in message:
        return "pooler_identity_rejected"
    if "not permitted to log in" in message:
        return "login_disabled"
    if "root certificate file" in message and ("does not exist" in message or "could not read" in message):
        return "tls_ca_missing"
    if any(part in message for part in ("certificate verify failed", "certificate verification failed", "does not match host name", "self-signed certificate", "unable to get local issuer certificate")):
        return "tls_certificate_rejected"
    if any(part in message for part in ("could not translate host name", "name or service not known", "temporary failure in name resolution")):
        return "hostname_resolution_failed"
    if isinstance(inner, TimeoutError) or "timeout expired" in message or "connection timed out" in message:
        return "connection_timeout"
    if any(part in message for part in ("connection refused", "network is unreachable", "server closed the connection unexpectedly")):
        return "connection_unavailable"
    if state == "42501":
        return "database_permission_denied"
    if state in ("42P01", "3F000"):
        return "database_schema_missing"
    if isinstance(state, str) and state.startswith("08"):
        return "connection_unavailable"
    if state is not None:
        return "database_error"
    return "diagnostic_internal_error"
