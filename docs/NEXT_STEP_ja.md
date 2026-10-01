# 次の操作：DB接続だけを確認する

## 今回の変更

PR #1の実行コードをmainに取り込み、mainのオフラインCI成功を確認しました。
Supabaseには専用アカウント用の `portfolio_os_worker` を作成しました。
作成時は **NOLOGIN・パスワードなし** です。管理者のパスワードは変更していません。
作成APIは成功しましたが、その後の詳細な確認クエリはツールの安全確認で停止しました。
そのため、新しいアカウントの実接続と詳細権限の確認は未完了です。

## 今回用意する接続確認の実行場所

GitHub Actionsの `Runtime preflight (read only)` を使います。
手動起動だけで、main以外では実行しません。AI課金・DB更新・案件情報の出力はありません。
通常のCIには本番の秘密情報を渡しません。
これは本番の自動運転用サーバーではなく、初回の接続確認用です。
定期運転とCodex実行はまだ開始しません。

## 本人が秘密情報を設定するところ

1. Supabase側で専用アカウント `portfolio_os_worker` のパスワードを設定し、LOGINを有効化します。
   管理者postgresのパスワードをアプリへ使わないでください。
   信頼できる管理用psql端末では `\password portfolio_os_worker` で対話入力し、続いて
   `ALTER ROLE portfolio_os_worker LOGIN;` を実行できます。
   GUIで設定する場合もSupabase側だけで行い、実際のパスワードをチャット・Git・PRへ貼りません。
   32文字以上のランダムなパスワードをパスワード管理ソフトで作成・保管してください。
2. SupabaseのConnect画面から **Session pooler** の接続情報を取得します。
   ホスト名は地域から推測せず、画面の値を使います。
   poolerのユーザー名は `portfolio_os_worker.<project-ref>`、パスワードは上で設定した専用パスワードです。
   DB名とポートはConnect画面に従います。URLには `sslmode=verify-full` を指定します。
   パスワードの特殊文字はURLエンコードしてください。
3. GitHubリポジトリの **Settings → Environments → portfolio-preflight** に進み、
   Environment secretsの **DATABASE_URL** に接続文字列を保存します。
   環境がまだない場合は同名で作成します。デプロイ対象をmainだけに制限することを推奨します。
   TLSで独自CAが必要なら、Supabaseから取得したCA証明書のPEM本文を **DATABASE_CA_CERT** として保存します。
   証明書利用時、URLに他端末の `sslrootcert` ファイルパスは含めないでください。

接続情報の例（実値ではありません。変更せず保存しないでください）：

```text
postgresql://portfolio_os_worker.<project-ref>:<encoded-password>@<session-pooler-host>:5432/postgres?sslmode=verify-full
```

## 設定後の依頼

このチャットに「DB接続情報を設定したので確認を実行してください」と依頼できます。
手動でもActions → Runtime preflight (read only) → Run workflowでmainを選び、確認欄を有効にして実行できます。
**接続文字列やパスワードそのものはメッセージに含めないでください。**

`SETUP_REQUIRED` は秘密情報が未設定です。`ready` は読み取り専用のDB接続と権限チェックだけの成功を意味します。
OpenAI API認証・DB書込み・Codex実行・事業の自動運転の成功ではありません。
接続が失敗した場合も、生の例外や診断結果は公開ログへ出さず、固定の結果だけを表示します。
詳細な原因の調査が必要なら非公開の管理環境で行います。

OpenAI APIキーとモデル名は、DB接続確認の後に別途設定します。このワークフローには渡しません。

## 適用済みロール作成SQLの控え

下記SQLはすでにSupabaseのマイグレーションとして適用しました。**再実行不要**です。
マイグレーションversionの取得・リポジトリ側ファイルとの同期は未完了です。

```sql
CREATE ROLE portfolio_os_worker
  NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 3 PASSWORD NULL;
GRANT portfolio_os_runtime TO portfolio_os_worker WITH INHERIT TRUE, SET FALSE;
ALTER ROLE portfolio_os_worker SET statement_timeout = '15s';
ALTER ROLE portfolio_os_worker SET lock_timeout = '5s';
ALTER ROLE portfolio_os_worker SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE portfolio_os_worker SET search_path = portfolio_os, pg_catalog;
ALTER ROLE portfolio_os_worker SET row_security = on;
COMMENT ON ROLE portfolio_os_worker IS 'Portfolio OS dedicated worker. NOLOGIN until owner provisions a private password. No DDL/DELETE and audit is append-only.';
```

## 参考資料

- Supabase Postgres Roles: https://supabase.com/docs/guides/database/postgres/roles
- Supabase Connect: https://supabase.com/docs/guides/database/connecting-to-postgres
- GitHub secrets: https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
- GitHub manual workflow: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
