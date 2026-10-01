# Portfolio OS — Phase 1 / v0.1.2

Single-owner, approval-gated portfolio manager using Python, OpenAI Responses API, Codex work briefs, and a private Supabase/PostgreSQL schema.

PR #1 is merged into main. The repository contains the engine, CLI, read-only runtime checks, HTML snapshots and offline tests. Source publication is not a running service: production execution remains paused.

**Next setup step:** [DB connection setup, Japanese](docs/NEXT_STEP_ja.md). A manual GitHub Actions workflow checks a dedicated database connection without model calls, database writes or private-record output. It stops when configuration is missing. It is not a scheduled production worker.

## Offline quick start

```sh
python -m venv .venv
# Activate .venv with the command appropriate to your operating system.
python -m pip install -e '.[dev]'
python -m pytest -q
python -m portfolio_os.cli demo
```

The demo uses synthetic records, SQLite and deterministic responses. It never connects to Supabase/OpenAI or launches Codex.

## Runtime preparation

Read `docs/NEXT_STEP_ja.md` and `docs/SETUP_ja.md`. Use a dedicated login, TLS certificate verification and private environment variables. The new worker identity was created as NOLOGIN with no password. Its actual login and detailed permissions remain unverified. Run the read-only preflight after securely configuring its credentials.

Imported projects are disabled. Only explicit owner commands can enroll/resume. A tick is bounded to three reviews and one task, with six reserved model requests per UTC day by default. Request limits are not currency spending guarantees. Human-active work has exactly three available database slots.

## Data boundary

This is a public code repository. Do not commit real business briefs, customer observations, patient/children's records, generated reports, API keys, database credentials or Codex authentication files. Private data stays in the dedicated database and an owner-controlled workspace. The manual preflight reports only allowlisted outcomes, never raw diagnostic output.

The initial database migrations and worker-identity creation are already applied in the existing deployment. Do not reapply them as new migrations. The new worker migration version still needs reconciliation; its applied SQL is recorded in `docs/NEXT_STEP_ja.md`.

## Not implemented / not verified

- Codex integration generates a work brief, not automatic code execution or deployment.
- No hosted approval UI or scheduled production worker is installed.
- Dedicated login, Python-to-Supabase connectivity and live OpenAI access still require verification.
- An offline test pass or green CI does not demonstrate production readiness or customer demand.

See `BUILD_STATUS.md` for the verification boundary and `AGENTS.md` for coding-agent constraints.
