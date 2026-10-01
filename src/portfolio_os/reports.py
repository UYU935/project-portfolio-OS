from __future__ import annotations
import html
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .workspace import safe_write

def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)

def date(value: int | None) -> str:
    if not value:
        return "—"
    return datetime.fromtimestamp(value, timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M")

def make_report(data: dict) -> str:
    projects = {p["id"]: p for p in data["projects"]}
    human = sum(p["mode"] == "HUMAN" for p in projects.values())
    ai = sum(p["mode"] == "AI" and p["automation_enabled"] for p in projects.values())
    pending = [a for a in data["approvals"] if a["status"] == "PENDING"]
    tasks = data["tasks"]
    queued = sum(t["status"] in ("QUEUED", "RUNNING") for t in tasks)
    run_count = len(data["runs"])
    mode = "DEMO · 外部接続なし" if data["demo"] else "SUPABASE · オーナー専用"
    banner = ("この画面は固定応答による動作デモです。AI・Web検索・Codexは実行していません。実案件の優先順位や市場評価ではありません。"
              if data["demo"] else "読み取り専用の保存時点レポートです。承認はオーナーのCLIから行います。このHTMLを公開しないでください。")
    project_rows = ""
    for p in sorted(projects.values(), key=lambda p: (p["human_slot"] or 9, p["name"])):
        project_rows += f"""<tr><td><strong>{esc(p['name'])}</strong><small>{esc(p['slug'])} · rev {p['revision']}</small></td>
        <td><span class="pill">{esc(p['stage'])}</span></td><td>{esc(p['mode'])}</td>
        <td>{'許可済み' if p['automation_enabled'] else '未許可'}</td><td>{date(p['next_review_at'])}</td></tr>"""
    approvals = ""
    for a in pending:
        p = projects[a["project_id"]]
        command = f"portfolio-os decide {a['id']} --digest {a['digest']} --approve --note '内容を確認' --confirm"
        approvals += f"""<article class="approval"><div class="eyebrow">要判断 · {esc(a['action'])}</div><h3>{esc(p['name'])}</h3>
        <p>{esc(a['reason'])}</p><div class="objection"><b>最も強い反論</b><p>{esc(a['objection'])}</p></div>
        <small>有効期限 {date(a['expires_at'])} · revision {a['project_revision']}</small>
        <details><summary>承認用CLIコマンドを見る</summary><pre>{esc(command)}</pre>
        <p class="muted">コマンドは自動実行されません。Human枠が満員の場合は、明示した入替が必要です。</p></details></article>"""
    if not approvals:
        approvals = '<p class="muted">現在、判断待ちの案件はありません。</p>'
    task_rows = ""
    for t in sorted(tasks, key=lambda t: t["created_at"], reverse=True):
        p = projects[t["project_id"]]
        detail = f"成果物: {t['artifact']}" if t["artifact"] else t["success_condition"]
        task_rows += f"<tr><td>{esc(p['name'])}<small>{esc(t['objective'])}</small></td><td>{esc(t['kind'])}</td><td><span class='pill'>{esc(t['status'])}</span></td><td>{esc(detail)}</td></tr>"
    events = ""
    for d in sorted(data["decisions"], key=lambda d: (d["created_at"], d["id"]), reverse=True)[:15]:
        p = projects.get(d["project_id"], {})
        events += f"<div class='event'><time>{date(d['created_at'])}</time><div><strong>{esc(d['event'])}</strong><small>{esc(p.get('name','SYSTEM'))} · {esc(d['actor'])}</small></div></div>"
    evidence_rows = ""
    for e in data["evidence"]:
        link = f"<a href='{esc(e['url'])}' rel='noreferrer noopener' target='_blank'>{esc(e['title'])}</a>" if e["url"] else esc(e["title"])
        evidence_rows += f"<tr><td>{link}<small>{esc(e['statement'])}</small></td><td>{esc(e['kind'])}</td><td>{'オーナー入力の顧客観測' if e['customer_signal'] else '需要の証拠とは未認定'}</td><td>{esc(e['limitation'])}</td></tr>"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; img-src 'none'">
    <title>Portfolio OS · Phase 1</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#f1f4f8;color:#182b3c;font:15px/1.7 system-ui,-apple-system,'Yu Gothic',Meiryo,sans-serif}}
    header{{background:#10293b;color:white;padding:34px max(6vw,24px)}}.head{{max-width:1220px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:24px}}
    .eyebrow{{font-size:11px;letter-spacing:.12em;font-weight:700;color:#447c80}}header .eyebrow{{color:#9ad2d0}}h1{{font-size:30px;margin:4px 0}}h2{{font-size:20px;margin:0 0 18px}}h3{{font-size:18px;margin:4px 0 12px}}p{{margin:8px 0}}small{{display:block;font-size:12px;color:#5f7280;overflow-wrap:anywhere}}
    header p{{color:#bfd0dd}}.badge{{border:1px solid #588092;padding:6px 12px;border-radius:20px;font-size:12px;white-space:nowrap}}
    main{{max-width:1270px;margin:24px auto;padding:0 24px 48px}}.banner{{background:#fff7e3;border-left:4px solid #bc8e2f;border-radius:6px;padding:14px 18px;margin-bottom:22px}}
    .metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:26px}}.metric,.panel{{background:white;border:1px solid #dce4ec;border-radius:12px;padding:22px}}.metric b{{font-size:34px;line-height:1.3;display:block;margin:8px 0}}.metric label{{font-size:12px;color:#5f7280}}.metric em{{font-size:18px;font-style:normal;color:#7d8c98}}.panel{{margin-bottom:22px}}.split{{display:grid;grid-template-columns:1.4fr 1fr;gap:22px;align-items:start}}.split>.panel:last-child{{max-height:560px;overflow:auto}}
    .scroller{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th{{font-weight:600;color:#697d8c;text-align:left;background:#f6f8fb}}th,td{{padding:13px 12px;border-bottom:1px solid #e6ecf1;vertical-align:top}}td:first-child{{min-width:230px}}.pill{{display:inline-block;padding:2px 9px;border-radius:12px;background:#edf4f7;color:#345e70;font-size:11px;font-weight:700;white-space:nowrap}}
    .approval{{border:1px solid #dce7ed;border-radius:8px;padding:18px;margin-top:14px}}.objection{{background:#f4f7fa;padding:10px 14px;border-radius:6px;margin:14px 0;font-size:13px}}.muted{{color:#6d7e8c;font-size:13px}}details{{margin-top:15px;font-size:13px}}summary{{cursor:pointer;color:#186b73}}pre{{background:#10293b;color:#e3f2f7;padding:14px;border-radius:7px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}.event{{display:flex;gap:15px;padding:12px 0;border-bottom:1px solid #e8edf2}}time{{font-size:12px;color:#79909f;min-width:72px}}.event strong{{font-size:12px;overflow-wrap:anywhere}}a{{color:#136e7b}}footer{{color:#6b8090;font-size:12px;text-align:center;padding:18px}}.rule{{font-size:13px;border-top:1px solid #e5ebf0;margin-top:18px;padding-top:14px}}
    @media(max-width:850px){{.split{{grid-template-columns:1fr}}.metrics{{grid-template-columns:repeat(2,1fr)}}.head{{align-items:flex-start;flex-direction:column}}main{{padding:0 14px}}.panel{{padding:17px}}}}
    </style></head><body><header><div class="head"><div><div class="eyebrow">VENTURE OPERATIONS / PHASE 1</div><h1>Portfolio OS</h1><p>AIは探索を進める。人間は集中する。</p></div><div class="badge">PHASE 1 · {mode}</div></div></header>
    <main><div class="banner">{banner}</div><section class="metrics"><div class="metric"><label>HUMAN ACTIVE</label><b>{human}<em> / 3</em></b><small>人間の関与枠はDBでも制限</small></div><div class="metric"><label>AI EXPLORATION</label><b>{ai}</b><small>許可済みのAI探索案件</small></div><div class="metric"><label>APPROVAL QUEUE</label><b>{len(pending)}</b><small>判断を待っている提案</small></div><div class="metric"><label>READY / RUNNING TASKS</label><b>{queued}</b><small>実行予約 {run_count} 件 · {'停止中' if data['paused'] else '有効'}</small></div></section>
    <section class="panel"><h2>案件台帳</h2><div class="scroller"><table><thead><tr><th>PROJECT</th><th>STAGE</th><th>MODE</th><th>AI実行許可</th><th>次回評価 JST</th></tr></thead><tbody>{project_rows}</tbody></table></div><div class="rule">INBOXは移行候補です。既存の事業進捗を推測して昇格しません。採点値・成功確率は作成していません。</div></section>
    <div class="split"><section class="panel"><h2>あなたの判断が必要なこと</h2>{approvals}</section><section class="panel"><h2>実行の記録</h2>{events}</section></div>
    <section class="panel"><h2>タスクと成果物</h2><div class="scroller"><table><thead><tr><th>PROJECT / OBJECTIVE</th><th>TYPE</th><th>STATUS</th><th>完了条件 / 成果物</th></tr></thead><tbody>{task_rows or '<tr><td colspan="4">タスクはまだありません。</td></tr>'}</tbody></table></div><p class="muted">CODEX_BRIEFの完了は作業依頼書の作成を意味し、Codex実行やアプリ完成ではありません。</p></section>
    <section class="panel"><h2>外部証拠と限界</h2><div class="scroller"><table><thead><tr><th>出典 / 観測</th><th>種類</th><th>需要の裏付け</th><th>限界</th></tr></thead><tbody>{evidence_rows or '<tr><td colspan="4">証拠は未登録です。仮説を事実に置き換えません。</td></tr>'}</tbody></table></div></section>
    <footer>生成時点 {date(data['generated_at'])} JST · 読み取り専用スナップショット · 自動更新・自動実行なし</footer></main></body></html>"""

def export_report(root: Path, data: dict, relative: str = "artifacts/portfolio-report.html") -> Path:
    return safe_write(root, relative, make_report(data))

def export_markdown(root: Path, data: dict) -> Path:
    lines = ["# Portfolio OS / 台帳の出力", "", "> 正本はDBです。このファイルを編集してもDBは更新されません。", ""]
    if data["demo"]:
        lines.extend(["> DEMO：AI評価・Web検索は未実行です。", ""])
    for p in data["projects"]:
        lines.extend([f"## {p['name']}", f"- ID: {p['id']}", f"- Mode / Stage: {p['mode']} / {p['stage']}", f"- Revision: {p['revision']}", f"- 次回: {date(p['next_review_at'])} JST", ""])
    return safe_write(root, "artifacts/portfolio.md", "\n".join(lines))
