# 次段階：OpenAI接続確認と1案件ドライラン

## 完了済み

- Supabase専用workerの実ログイン・読み取りpreflightは成功。
- クライアント→Session poolerはTLS + verify-fullで確認済み。
- 2026-10-02、portfolio_os_runtime権限で合成projectと監査行をトランザクション内に作成・更新し、ROLLBACKした。直後にproject/decisionが0件であることを確認した。実案件は変更していない。
- 全体停止は解除しない。

## 1. OpenAI認証preflight

GitHub Environment `portfolio-preflight` に Environment secret として `OPENAI_API_KEY` を保存する。チャット、コード、Issue、PRへ貼らない。

その後 Actions → **OpenAI auth preflight (no inference)** → Run workflow。
mainを選び、確認欄を有効化する。標準モデルは `gpt-6-sol`。

この処理は `GET /v1/models` のみで、Responses APIによる推論は実行しない。

## 2. 1案件だけのAIドライラン

実行前に、個人情報・患者情報・子どもの情報・秘密情報を含まない案件を1件だけ選ぶ。
その案件のslugを Environment secret `TRIAL_PROJECT_SLUG` として保存する。slug自体も公開ログへ出さない。

Actions → **One project AI dry run (read only DB)** → Run workflow。
main、2つの確認欄を有効化する。

この処理は:
- DBは読み取り専用
- Portfolio全体はpausedのまま
- automation_enabledが0件であることを要求
- Web検索なし
- Codex実行なし
- 外部送信なし
- 1回だけモデル評価
- DBへ評価結果を書き込まない

公開ログには recommendation とtoken数だけを出し、案件内容・モデル理由・接続情報は出さない。

## 次の段階

このドライラン成功後に、初めて「1案件の評価結果をDBへ記録する限定試運転」を別ゲートとして設計する。定期実行はさらにその後。
