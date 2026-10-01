# DB接続診断の修正 — 2026-10-02

手動実行 run 36895444029 は、秘密情報の存在確認と依存パッケージの導入に成功し、接続診断で停止しました。公開された結果は `diagnostic_failed` だけであり、元のDB接続エラーは特定できません。パスワード誤りとは断定しません。

## 修正

- doctorは接続失敗でもJSONを返し、公開用ラッパーは決められたエラー種別だけを表示します。生の例外、ホスト名、ユーザー名、パスワード、SQL、案件内容は表示しません。
- CAが指定されていない場合、実行環境にあるOSの信頼済みCAファイルをlibpqへ渡します。明示されたCAは維持し、`sslmode=verify-full`を弱めません。独自CAが必要なら引き続き `DATABASE_CA_CERT` が必要です。
- 読み取り専用、手動・main限定、外部モデル不使用、DB更新禁止は変更しません。

## 再確認

修正後のmainで、Actions → Runtime preflight (read only) → Run workflowから新しく実行してください。以前の失敗画面のRe-run jobsでは元のコミットを使うため、今回の修正が入りません。パスワードやDATABASE_URLを闇雲に変更する必要はありません。

新しい診断の結果が出るまで、実接続成功とは扱いません。`authentication_failed`も認証失敗の分類であり、どの入力値が誤っているかまでは示しません。認証確認後も本番自動運転・OpenAI APIの実行は別工程です。

## 根拠

- PostgreSQL 17 libpq SSL: https://www.postgresql.org/docs/17/libpq-ssl.html
- Supabase database connection: https://supabase.com/docs/guides/database/connecting-to-postgres
