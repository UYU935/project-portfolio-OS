# 1案件・評価記録パイロット

## 目的

OpenAIの1回の評価結果を実DBへ保存し、監査履歴を残せることを確認する。
この段階では評価内容を案件の状態へ適用しない。

## 保存するもの

- `agent_runs`: provider/model/status、入出力token、request id、評価JSON
- `decisions`: 予約・成功・失敗のappend-only監査イベント

## 変更しないもの

- project.stage
- project.mode
- project.human_slot
- project.automation_enabled
- project.revision
- tasks / approvals
- Portfolio全体のpaused状態

Web検索、Codex、定期実行、外部メッセージ送信も行わない。

## 失敗時

API呼出し前にrunをRESERVEDとして記録する。
明確な失敗はFAILED、不確実なネットワーク/5xx系はUNCERTAINにする。
自動再試行はしない。

## 実行条件

GitHub Actionsの `One project persisted evaluation pilot` をmainから手動起動する。
3つの確認欄（有料1回、評価記録、サニタイズ済み案件）をすべて有効化する。
対象は既存のEnvironment secret `TRIAL_PROJECT_SLUG` を使うため、Secretの再入力は不要。

成功後はSupabase側で、run/decisionが1件分記録され、案件状態が不変であることを再確認する。
