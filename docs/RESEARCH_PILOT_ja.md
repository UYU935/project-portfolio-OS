# 1案件・Web調査Evidence保存パイロット

## 目的

1案件について、OpenAI Responses APIのWeb検索を最大1回だけ使用し、
取得した公開情報をEvidenceとしてSupabaseへ保存できることを確認する。

## 保存するもの

- agent_runs: RESEARCH run、token数、request id、構造化された調査結果
- evidence: providerが実際に取得したsource URLに一致するfindingだけ
- decisions: 予約・成功・失敗のappend-only監査履歴

## Evidenceの扱い

WEB_RETRIEVEDは公開Webから取得した根拠であり、
顧客の利用・支払意思を確認した証拠ではない。
customer_signalはfalseのまま保存する。
同じURLはfingerprintで重複保存しない。

## 変えてよいもの

Evidenceが追加された場合だけproject.revisionを+1し、updated_atを更新する。
これは案件スナップショットの変更検知のため。

## 変更しないもの

- project.stage
- project.mode
- project.human_slot
- project.automation_enabled
- Portfolio全体のpaused状態
- approvals/tasks

Codex、定期実行、外部メッセージ送信、顧客接触は行わない。

## 実行

GitHub Actionsの `One project research evidence pilot` をmainから手動実行。
既存の `TRIAL_PROJECT_SLUG`、DB/API Secretsを再利用するためSecret再入力不要。
3つの確認欄（有料Web調査、Evidence保存、サニタイズ済み）をすべて有効化する。
