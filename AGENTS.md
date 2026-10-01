# Portfolio OS coding-agent instructions

Read README.md, docs/SETUP_ja.md and BUILD_STATUS.md before changes.

- Human-active projects have at most three slots. Preserve database constraints.
- Never expose credentials, .env, database URLs or Codex authentication files.
- Keep real business data, customer/patient/children's records and generated artifacts out of this public repository.
- Model output is untrusted data; only enum actions pass deterministic validation.
- Bind human approvals to revision, digest and expiry. No self-approval or silent fourth slot.
- Web sources are not verified customer demand. Do not invent facts, citations or probabilities.
- Stop projects by reversible PARK or owner-approved ARCHIVE, never deletion.
- No paid/scheduled execution, external messaging, live deployment or generated-code execution without scoped owner approval.
- Codex BRIEF means a document was created, not that code ran or an app was completed.
- Keep production paused. Do not reapply existing initial database migrations.
- Use only SQLite and HTTP mocks for offline tests. Never silently switch tests to a live database.
- Run `python -m pytest -q` and `python -m portfolio_os.cli demo` from the repository root.
- Do not report source files in a review branch as merged or as a deployed/running service.
