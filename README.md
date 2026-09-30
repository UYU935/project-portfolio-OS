# Portfolio OS — Phase 1

A single-owner portfolio manager designed around Python, OpenAI Responses API, Codex work briefs, and a private Supabase/PostgreSQL schema.

## Verified deployment status — 2026-09-30

- This branch currently contains documentation only. The executable application has **not** been published to a branch or a pull request.
- The initial bulk source-code publication was stopped by the tool's safety check. It was not bypassed or treated as successful.
- The dedicated database schema has been created with seven tables, row-level security, restricted service permissions, and a three-slot constraint for human-active projects.
- Two database migrations are recorded. Restricted-role tests verified that a fourth human slot is rejected and that the service role cannot delete records or rewrite the audit table. Test records were rolled back.
- The database remains paused. No paid model calls, scheduled worker, external publication, or Codex execution has been enabled.
- The revised local package passed 68 offline tests. This is **not** a successful GitHub Actions run and does not establish live OpenAI or Python-to-PostgreSQL connectivity.

## Data boundary

This is a public code repository. Real business briefs, customer observations, research results, generated artifacts, credentials, and personal data must not be committed here. Private records remain in the private database or an owner-controlled workspace.

## Remaining work

Review and complete source publication; configure a dedicated database login and OpenAI API credentials in a trusted execution environment; run one bounded integration test; only then consider scheduled execution. Account connection in ChatGPT is separate from runtime credential configuration.

Codex support in phase 1 means generating a work brief, not automatically running generated code or building a completed application. No review branch, application CI result, or completed deployment should be inferred from this README.
