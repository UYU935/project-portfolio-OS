# 起動前の設定 / v0.1.2

## 現在の境界

既存Supabaseには専用スキーマと案件候補が登録済みです。初期マイグレーションを新たに再実行しないでください。本番の停止解除・案件選択・有料API起動は、コード配置とは別の操作です。

## ローカル確認

Python 3.11以上で仮想環境を作り、リポジトリ直下から実行します。

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
python -m portfolio_os.cli demo
```

デモは架空データ・SQLite・固定応答だけを使用します。実案件や外部APIに触れません。

## 専用DBログインと秘密情報

初回はオーナー管理のPCなど、成果物を永続保存できる非公開環境で手動試行します。`portfolio_os_runtime`はNOLOGINの権限グループです。外部実行には別の専用LOGINとパスワードの設定が必要です。管理者postgresを実行用に使わないでください。

管理者がSupabaseの安全な管理画面・端末で専用LOGINを作成し、`portfolio_os_runtime`の権限を付与します。パスワードをチャット・Git・PRへ貼りません。既存postgresのパスワード変更は不要です。

接続文字列はSupabaseのConnect画面から取得します。IPv4環境ではSession poolerを使い、ホスト名を地域から推測しません。専用ユーザー名の接尾辞、特殊文字のURLエンコードを確認します。TLSは`sslmode=verify-full`を使用し、必要ならダッシュボードから取得した信頼できるCA証明書を`sslrootcert`で指定します。

```sh
python -m pip install -e '.[live]'
```

`.env.example`を非公開の`.env`にコピーし、`DATABASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`を端末上で設定してください。APIモデル名は自分のOpenAI APIプロジェクトで利用可能なものを明示します。ChatGPTの連携認証は外部プログラムのAPI認証とは別です。

## 無課金の接続チェック

```sh
portfolio-os doctor
portfolio-os doctor --connect
```

`--connect`は読み取り専用トランザクションで、接続・TLS・専用ログイン・非管理者権限・RLS・削除禁止・監査履歴更新禁止・初期状態を確認します。秘密情報や案件内容は表示せず、モデルも呼びません。不備があれば終了コード2です。この検査はOpenAIの利用権限やDB書込みの成功を保証しません。

`status`と`report`は私的な案件内容を含みます。公開ログやGitHub Actionsへ流さないでください。

## 初回の有料試行は別の承認後

個人情報のない1案件をオーナーが選び、要約と送信範囲を確認します。`enroll`と`resume`の両方が必要です。この文書の掲載は実行許可ではありません。

```text
portfolio-os enroll <確認済みslug> --confirm
portfolio-os resume --confirm
portfolio-os tick
portfolio-os pause
portfolio-os report
```

出典・最も強い反論・利用量・タスク結果を確認します。回数上限は金額上限ではありません。完了不明のAPI呼出しは自動再試行しません。

## 定期稼働

初回の結合試験・停止・復旧を検証するまでスケジュールを登録しません。リポジトリのCIはオフライン試験専用で、秘密情報・本番DB・有料APIを使いません。

公式資料: Supabase Connect to your database / Postgres Roles、OpenAI Responses API / Web search。接続仕様は導入時に公式資料を再確認してください。
