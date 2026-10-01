from __future__ import annotations
import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from .approvals import create_approval, decide
from .codex import version as codex_version
from .config import Config
from .db import Store
from .diagnostics import inspect_runtime
from .preflight_errors import classify_error
from .domain import Evaluation, ProjectInput, RuleError
from .engine import tick
from .models import Project
from .provider import DemoProvider, OpenAIProvider
from .reports import export_markdown, export_report

def confirm(args) -> None:
    if not getattr(args, "confirm", False):
        raise RuleError("This change needs --confirm. Review the operation first.")

def live() -> tuple[Config, Store]:
    cfg = Config.load()
    return cfg, Store(cfg.database_url)

def run_demo(root: Path, output: Path | None = None) -> Path:
    import uuid
    output = (output or root / ".local" / f"demo-{uuid.uuid4().hex[:8]}").resolve()
    output.mkdir(parents=True, exist_ok=True)
    db_path = output / "demo.db"
    if db_path.exists():
        raise RuleError("Demo destination already contains a DB; use a new directory")
    store = Store(f"sqlite:///{db_path}", demo=True)
    store.initialize_demo()
    samples = [
        ProjectInput(slug="demo-a", name="【デモ】体験学習の企画", summary="動作確認用の架空案件。市場評価ではない。", domain="education"),
        ProjectInput(slug="demo-b", name="【デモ】小規模業務アプリ", summary="動作確認用の架空案件。実装は依頼書まで。", domain="software"),
        ProjectInput(slug="demo-c", name="【デモ】地域サービス", summary="動作確認用の架空案件。昇格の承認待ちを試す。"),
    ]
    for p in samples:
        store.register(p)
        store.enroll(p.slug)
    store.add_observation("demo-c", "【DEMO】承認画面を表示するための固定入力。実在顧客の観測ではありません。")
    policy = Config(root, "", demo=True).policy()
    store.pause(False)
    asyncio.run(tick(store, DemoProvider(), output, policy))
    asyncio.run(tick(store, DemoProvider(), output, policy))
    store.pause(True)
    data = store.export()
    report = export_report(output, data, "report.html")
    export_markdown(output, data)
    (output / "state.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Portfolio OS phase 1: bounded, approval-gated execution")
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("demo", help="Offline fixture, never accesses APIs or production DB")
    d.add_argument("--output", type=Path)
    d = sub.add_parser("doctor", help="Check configuration without printing secrets or calling models")
    d.add_argument("--connect", action="store_true", help="Read-only PostgreSQL connection and privilege checks")
    sub.add_parser("status")
    r = sub.add_parser("report", help="Export a private read-only HTML snapshot")
    r.add_argument("--output", default="artifacts/portfolio-report.html")
    imp = sub.add_parser("import-projects", help="Import INBOX entries only, never overwrite existing state")
    imp.add_argument("file", type=Path)
    imp.add_argument("--confirm", action="store_true")
    for name in ("enroll", "brief"):
        a = sub.add_parser(name)
        a.add_argument("slug")
        if name == "brief":
            a.add_argument("file", type=Path)
        a.add_argument("--confirm", action="store_true")
    sub.add_parser("pause")
    for name in ("resume", "recover"):
        a = sub.add_parser(name)
        a.add_argument("--confirm", action="store_true")
    sub.add_parser("tick", help="One bounded iteration; requires resume plus enrolled projects")
    o = sub.add_parser("observe")
    o.add_argument("slug")
    o.add_argument("--text", required=True)
    o.add_argument("--url")
    o.add_argument("--customer-signal", action="store_true")
    o.add_argument("--confirm", action="store_true")
    a = sub.add_parser("request", help="Owner creates a reviewable state-change proposal")
    a.add_argument("slug")
    a.add_argument("action", choices=["HUMAN_ACTIVE", "ADVANCE", "PARK", "ARCHIVE"])
    a.add_argument("--target-stage")
    a.add_argument("--reason", required=True)
    a.add_argument("--confirm", action="store_true")
    a = sub.add_parser("decide")
    a.add_argument("approval_id")
    a.add_argument("--digest", required=True)
    choice = a.add_mutually_exclusive_group(required=True)
    choice.add_argument("--approve", action="store_true")
    choice.add_argument("--reject", action="store_true")
    a.add_argument("--replace", dest="replace_slug")
    a.add_argument("--note", required=True)
    a.add_argument("--confirm", action="store_true")
    for name in ("acknowledge-run", "retry-task"):
        a = sub.add_parser(name)
        a.add_argument("id")
        a.add_argument("--confirm", action="store_true")
        if name == "acknowledge-run":
            a.add_argument("--note", required=True)
    return p

def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "demo":
            report = run_demo(Path.cwd(), args.output)
            print(f"DEMO ONLY / no external calls\n{report}")
            return 0
        if args.command == "doctor":
            info = {"python": sys.version.split()[0], "codex": codex_version(),
                    "psycopg_installed": importlib.util.find_spec("psycopg") is not None,
                    "paid_calls_made": 0, "database_connection_tested": False}
            try:
                cfg = Config.load()
                cfg.policy()
                info.update({"database_config": "present (credentials hidden)", "openai_key": bool(cfg.api_key), "openai_model": cfg.model or "NOT_SET"})
            except Exception as exc:
                info["error_code"] = classify_error(exc, configuration=True)
                print(json.dumps(info, ensure_ascii=False, indent=2))
                return 2
            if args.connect:
                store = None
                try:
                    store = Store(cfg.database_url)
                    info["database"] = inspect_runtime(store)
                    info["database_connection_tested"] = True
                except Exception as exc:
                    # Keep a valid JSON result even when connection setup fails.
                    # Neither exception text nor connection credentials leave here.
                    info["error_code"] = classify_error(exc)
                    print(json.dumps(info, ensure_ascii=False, indent=2))
                    return 2
                finally:
                    if store is not None:
                        store.engine.dispose()
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0 if not args.connect or info["database"]["database_ready"] else 2
        cfg, store = live()
        preflight = inspect_runtime(store)
        if not preflight["database_ready"]:
            raise RuleError("Runtime preflight failed: " + ", ".join(preflight["failed_checks"]))
        policy = cfg.policy()
        if args.command == "status":
            print(json.dumps(store.export(), ensure_ascii=False, indent=2))
        elif args.command == "report":
            data = store.export()
            print(export_report(cfg.root, data, args.output))
            export_markdown(cfg.root, data)
        elif args.command == "import-projects":
            confirm(args)
            items = json.loads(args.file.read_text(encoding="utf-8-sig"))
            valid = [ProjectInput.model_validate(item) for item in items]
            for item in valid:
                store.register(item)
            print(f"Imported or already present: {len(valid)}. INBOX / disabled by default.")
        elif args.command == "enroll":
            confirm(args)
            store.enroll(args.slug)
            print("Enrolled. This sanitized brief and observations may be sent to OpenAI after resume.")
        elif args.command == "brief":
            confirm(args)
            store.update_brief(args.slug, args.file.read_text(encoding="utf-8-sig"))
            print("Brief updated; old queued work and pending approvals invalidated.")
        elif args.command == "pause":
            store.pause(True)
            print("Paused. No new calls; an already-started API call may still be billed.")
        elif args.command == "resume":
            confirm(args)
            if not cfg.api_key or not cfg.model:
                raise RuleError("OPENAI_API_KEY / OPENAI_MODEL is missing")
            store.pause(False)
            print(f"Enabled. Up to {policy.max_paid_requests_per_day} reserved API requests/day (UTC); not a currency spending guarantee.")
        elif args.command == "recover":
            confirm(args)
            print(json.dumps(store.recover(), ensure_ascii=False))
        elif args.command == "tick":
            out = asyncio.run(tick(store, OpenAIProvider(cfg.api_key, cfg.model, cfg.root), cfg.root, policy))
            print(json.dumps(out, ensure_ascii=False, indent=2))
            export_report(cfg.root, store.export())
        elif args.command == "observe":
            confirm(args)
            print(store.add_observation(args.slug, args.text, customer_signal=args.customer_signal, url=args.url))
        elif args.command == "request":
            confirm(args)
            ev = Evaluation(recommendation=args.action, reason=args.reason,
                strongest_objection="オーナーの直接提案。採用前に反証・機会費用を確認してください。",
                uncertainties=["オーナー確認が必要"], evidence_ids=[], next_step="オーナーによる承認または却下",
                success_condition="台帳と意思決定が一致する", target_stage=args.target_stage, review_in_days=7)
            with store.tx() as s:
                p = s.scalar(select(Project).where(Project.slug == args.slug))
                if not p:
                    raise RuleError("Unknown project")
                approval = create_approval(s, p, ev, policy)
                if approval:
                    print(json.dumps({"approval_id": approval.id, "digest": approval.digest}, ensure_ascii=False))
                else:
                    raise RuleError("Approval queue is full")
        elif args.command == "decide":
            confirm(args)
            print(decide(store, args.approval_id, args.digest, approve=args.approve, note=args.note, replace_slug=args.replace_slug))
        elif args.command == "acknowledge-run":
            confirm(args)
            store.resolve_uncertain(args.id, args.note)
            print("Unknown completion acknowledged. Reservation remains counted; no automatic retry.")
        elif args.command == "retry-task":
            confirm(args)
            store.retry_task(args.id)
            print("Retry explicitly authorized. A new API reservation is required if research runs again.")
        return 0
    except (RuleError, ValueError, OSError, ImportError, SQLAlchemyError) as exc:
        message = str(exc) if isinstance(exc, RuleError) else type(exc).__name__
        print(f"STOP: {message}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
