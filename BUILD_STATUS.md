# 構築・検証状況 — 2026-10-01 / v0.1.2 + 起動準備

## 完了を確認した項目

- PR #1をmainへマージしました。マージコミットは `6abcfa536be06d1f67e35684c4544d48b651f182` です。
- mainのGitHub Actions `Offline checks`（run `36867848419`）はsuccessでした。
- Supabaseの直近の読み取り確認では全体停止、7案件、自動実行許可0、実行履歴0でした。
- `portfolio_os_worker_identity` マイグレーションの作成APIはsuccessでした。専用アカウント用ロールはNOLOGIN、パスワードなしで作成するSQLです。
- 公開ログの漏出防止・未設定時停止などの追加テスト14件をローカルで実施し、成功しました。

## この変更で追加するもの

GitHub Actions `Runtime preflight (read only)` と公開ログ用ラッパー、追加テスト、本人向け設定手順です。
mainからの手動確認だけに制限し、モデルキーを渡さず、DBへは既存の読み取り専用doctorで接続します。
DB接続情報が未設定の場合はSETUP_REQUIREDで停止します。
このコミットの全体CI結果と手動ワークフロー結果は、GitHub Actionsで別途確認してください。

## 未完了・検証上の限界

ロール作成後の詳細確認クエリはOpenAI側の安全チェックで停止しました。権限の再確認を成功と扱いません。
新規マイグレーションversionの取得・ソースへの同期も未完了です。SQL控えは `docs/NEXT_STEP_ja.md` にあります。
専用パスワードの設定・LOGIN有効化・GitHub Environment secretsの登録・Pythonから実DBへの接続・実DB書込み・OpenAI API・Codex実行・定期稼働・承認Web UIは未完了です。

今回の操作は停止解除・案件選択・顧客接触・有料API起動を含みません。
