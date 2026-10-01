# Portfolio OS — Phase 1 / v0.1.2

Single-owner, approval-gated portfolio manager using Python, OpenAI Responses API, Codex work briefs, and a private Supabase/PostgreSQL schema.

This review branch contains the executable engine, read-only runtime preflight, CLI, HTML snapshots and offline tests. Source publication is not a running service: production execution remains paused.

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

Read `docs/SETUP_ja.md` before configuring anything. Install the `live` extra only in the owner-controlled execution environment. Use a dedicated login, TLS certificate verification, and private environment variables. Run `portfolio-os doctor --connect` first; it performs read-only database and permission checks, not a paid API test.

Imported projects are disabled. Only explicit owner commands can enroll/resume. A tick is bounded to three reviews and one task, with six reserved model requests per UTC day by default. Request limits are not currency spending guarantees. Human-active work has exactly three available database slots.

## Data boundary

This is a public code repository. Do not commit real business briefs, customer observations, patient/children's records, generated reports, API keys, database credentials or Codex authentication files. Private data stays in the dedicated database and an owner-controlled workspace.

The initial database migrations are already applied in the existing deployment. Do not reapply them as new migrations. This version makes no production schema/data changes.

## Not implemented / not verified

- Codex integration generates a work brief, not automatic code execution or deployment.
- No hosted approval UI or scheduled production worker is installed.
- Runtime login, Python-to-Supabase connectivity and live OpenAI access still require verification.
- An offline test pass or a green CI run does not demonstrate customer demand, production readiness, or success of a business.

See `BUILD_STATUS.md` for the verification boundary and `AGENTS.md` for coding-agent constraints.
