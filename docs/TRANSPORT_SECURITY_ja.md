# 接続診断のTLS確認範囲 — 2026-10-02

## 修正の理由

証明書追加後の手動診断はDB認証と12項目の権限・状態確認に成功し、
`encrypted_connection`だけがfalseでした。旧診断はPostgreSQLの
`pg_stat_ssl`を、クライアントから接続プールまでのTLS状態として扱っていました。
接続プールを挟む場合、この2つの接続は別です。

## 判定するもの

`encrypted_connection`は、実際に接続したpsycopgの
`Connection.pgconn.ssl_in_use is True`と、同じ接続の
`Connection.info.get_parameters()['sslmode'] == 'verify-full'`を要求します。
閉じた接続、取得不能、型不一致、弱いsslmodeは失敗扱いです。
Config.loadによるverify-full要求と証明書検証は維持します。
URLの文字列だけ、接続成功だけ、ホスト名の文字列だけでは合格にしません。

`transport.postgres_backend_tls`には、サーバー側pg_stat_sslの実測値を
別項目として残します。false/不明は公開ログにも固定の警告
`postgres_backend_tls_not_confirmed`を出します。trueへ書き換えません。

## この診断で保証しないもの

`ready`はクライアント側TLS・認証・既存の権限確認が通った意味です。
接続プールからPostgreSQLまでの内部経路の保護、プロキシが内部で
実施する証明書検証、経路全体のTLSを保証しません。
`end_to_end_tls_verified`はfalseのままです。内部側がfalse/不明なら、
機密データを扱う本番運転の承認前に内部通信の保護を別途確認します。
必要に応じてTLSを終端まで検証できる直接接続等を検討します。

今回の変更は検証を無効化して成功扱いするものではありません。
DB権限・スキーマ・パスワード・秘密情報・停止状態・実行トリガーを
変更せず、課金APIや書き込みも実行しません。

## 再確認

修正コミットをmainへ反映した後、Runtime preflight (read only)の
Run workflowからmainを選び、新しい実行を開始します。
過去runのRe-runは過去のコミットを再実行するため使いません。
オフライン回帰テストと実接続確認は別に記録します。

## 公式根拠

- PostgreSQL pg_stat_ssl: https://www.postgresql.org/docs/17/monitoring-stats.html#MONITORING-PG-STAT-SSL-VIEW
- libpq verify-full: https://www.postgresql.org/docs/17/libpq-ssl.html
- Psycopg 3.3.4 SSL accessor: https://github.com/psycopg/psycopg/blob/3.3.4/psycopg/psycopg/pq/abc.py
- Psycopg active connection options: https://github.com/psycopg/psycopg/blob/3.3.4/psycopg/psycopg/_connection_info.py
- Supabase connection pooling: https://supabase.com/docs/guides/database/connecting-to-postgres
