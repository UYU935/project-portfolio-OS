# Evidence再評価 → Approvalパイロット

## 目的

保存済みEvidenceをPortfolio Managerへ渡して1回だけ再評価し、
人間承認が必要な提案だけをPENDING Approvalとして保存する。

AIの提案はこの段階では適用しない。

## Approvalを作る提案

- HUMAN_ACTIVE
- ADVANCE
- PARK
- ARCHIVE

ただし既にPARKEDの案件へのPARKなど、明らかな重複提案はApprovalを作らずHOLD相当として記録する。

## Approvalを作らない提案

- HOLD
- RESEARCH
- CODEX_BRIEF

これらを無理にApprovalへ変換しない。AI判断を歪めないため。

## 安全境界

- Portfolio全体はpausedのまま
- project.stage/mode/automation_enabled/human_slot/revisionは変更しない
- Web検索なし（既存Evidenceだけを読む）
- Codexなし
- task自動作成なし
- 外部送信なし
- 定期実行なし
- pending Approvalが既にある場合は実行を拒否
- モデルが入力にないEvidence IDを引用した場合は失敗扱い

成功後、Approvalが作られた場合でも、適用は別のオーナー承認ゲートで行う。
