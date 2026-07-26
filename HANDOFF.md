# 引き継ぎ書（HANDOFF）— NOTIME 日次ETL

> このファイルは「作業を引き継ぐ人（人間でもクラウドのClaudeでも）」が
> **最初に読む**ためのものです。まずここ → 次に `README.md` の順で読んでください。

---

## ✅ 完了（2026-07-26）— 本番データ投入まで確認済み
- **cashier**: ログイン→検索オプション展開→期間指定→検索→「CSV出力(明細)」
  →**ダウンロード形式モーダル内のダウンロードボタン**→CSV取得→Supabase保存、
  まで GitHub Actions 上で成功。**7/26 分 164行を `sales` に投入確認**。
- **デジテール**: 山形139人/いわき75人/福井130人（7/25）を `visits` に投入確認。
- ★2（cashierのセレクタ確定）は**解決済み**。要点は「CSV出力ボタンは
  data-toggle=modal で“形式選択モーダルを開くだけ”。実DLはモーダル内のボタン」。
- 毎朝6時(JST)の自動実行はデフォルトブランチのコードで動く（反映済み）。
- 既知の小メモ: 対象日にデータが無い日は `no_data`（正常）。Supabase通信は
  一時的なタイムアウトに備えて3回リトライを入れた。

---

## 進捗更新（2026-07-26）
- **GitHub Actions に「NOTIME 日次ETL」が登録され Active**。手動起動できる状態。
  スモークテストで checkout→pip→Playwright(Chromium) 導入まで成功を確認。
  （Secrets未登録のため「設定確認」ステップで意図通り停止するところまで検証済み）
- **改良コードをデフォルトブランチ `feature/supabase-etl` に反映済み**
  （毎朝6時の自動実行はデフォルトブランチのコードで動くため）。
- **失敗時の画面調査を自動化**：`etl/browser.py` の `dump_page` が
  入力欄・ボタンの一覧を debug/ の `<label>_report.txt` に保存し、実行ログにも出す。
  → ★2（cashierセレクタ確定）が「Actionsを1回回す→ログ/Artifactを見る」で閉じる。
- **残るはユーザー作業2つのみ**（アカウント/パスワードが要るため代行不可）:
  ① Supabaseで `sql/setup_all.sql` を実行 ／ ② GitHubにSecretsを6つ登録。
  この2つが済めば、起動・失敗解析・cashier確定・再実行・完成まで自動で進められる。

---

## いまどこまで出来ているか（2026-07-23 時点）

**目的**: 毎朝6時（日本時間）に、前日ぶんの
① cashier の売上明細 と ② デジテールの来店数 を自動で集めて Supabase に保存する。

**方式**: Python + Playwright（画面を出さないChrome）でログインしてCSVを取得
→ Supabase（PostgREST）へ保存。実行は GitHub Actions の cron。GCPは使わない。
認証情報はすべて GitHub Actions の Secrets（コードには一切書かない）。

**このブランチ**: `feature/supabase-etl`（`main` から分岐）。

### 実装済み・動作確認済み
- `etl/` 一式（ログイン→CSV取得→変換→Supabase保存→ingest_log記録）
- `sql/001_schema.sql` `sql/002_seed_stores.sql`（テーブル定義と店舗マスタ）
- `.github/workflows/daily-etl.yml`（毎朝6時JST + 手動実行ボタン）
- `README.md`（日本語。Secrets登録と手動実行の手順）
- ダミーCSVでの変換テストは通過済み（`etl/rows.py` の解析ロジック）

### まだ実際のサイト／DBでは動かしていない（＝次にやること）
下の「次にやること」を参照。

---

## 次にやること（優先順）

### ★1. Supabase の準備と Secrets 登録（ユーザーShoさんのGitHub/Supabase作業）
- `sql/001_schema.sql` → `002_seed_stores.sql` を Supabase の SQL Editor で実行
- GitHub の Secrets に6つ登録（`README.md` の1章に手順）:
  `CASHIER_ID` `CASHIER_PW` `DIGITAIL_ID` `DIGITAIL_PW`
  `SUPABASE_URL` `SUPABASE_SERVICE_KEY`
- ⚠️ パスワード・キーはClaudeに見せない。Secretsに直接入れれば見せる必要はない。

### ★2. cashier のログイン後の画面（期間欄・CSVボタン）を確定させる
これが**今いちばんの不確定点**。ログインしないと画面が見えないため、
`etl/cashier_fetch.py` は「よくある形」を順に試す作りになっている。

**スマホからでも確定できる方式に改善済み（PCログイン不要）**:
★1のSecrets登録が終わっていれば、GitHub Actions が本物のSecretsで
cashierにログインし、目印が見つからなければ debug/ に
**入力欄・ボタンの一覧＋通信の記録** を自動保存する（`etl/browser.py` の
`dump_page` が `<label>_report.txt` を書く）。
手順:

1. Actions →「NOTIME 日次ETL」→ Run workflow（`only=cashier` でよい）
2. 赤くなったら Artifacts の `debug-…` を落とす
3. `cashier_daterange_notfound_report.txt`（または `..._csv_button_notfound_report.txt`）
   の中身を共有 → セレクタを確定させる

材料が揃ったら `etl/cashier_fetch.py` の候補、または `.env` の
`CASHIER_DATE_FROM_SELECTOR` / `CASHIER_DATE_TO_SELECTOR` / `CASHIER_CSV_SELECTOR`
を確定させる。CSVが「あるURLへのGET」だと分かれば、デジテールと同じく
`context.request.get(...)` で直接叩く堅い作りに切り替えるのが理想。

**手元PCが使える場合（従来の方法）**: `python tools/inspect_cashier.py` を実行
→ 自分でログイン → 取引一覧を表示 → Enter → `debug/cashier_report.txt` を共有。
（inspect も改善後は `etl/browser.py` の scan を共用している。出力内容は同じ）

### 3. 実際に1日ぶんを流して確認
Secrets登録後、GitHubの Actions → 「NOTIME 日次ETL」→ Run workflow（date指定可）。
または手元で `python -m etl.run_daily --date 2026-07-20 --headed`。
→ Supabase の `ingest_log` を見て success になっているか確認。

### 4. （将来）Airレジ/EZレジ（下北沢）の売上取得
今回のETLは cashier の3店舗（山形・いわき・福井）が対象。
下北沢は POSが別（Airレジ+EZレジ）。`adapters.py` に読み解きは実装済みだが、
「サイトからCSVを取る」部分は未着手。必要になったら
`etl/cashier_fetch.py` と同じ作りで `airregi_fetch.py` / `ezregi_fetch.py` を足す。

---

## 触ってはいけないファイル（変更禁止）
`config.py` / `adapters.py` / `transform.py` / `db.py`
→ ユーザーが実データで検証済み・完成済みと明言。
今回のETLは `adapters.adapt_cashier()` を**読み取り専用で再利用**している。
売上CSVの解釈（セット割の按分・店舗名の表記ゆれ）を二度書かないため。

## 重要な落とし穴（実データで判明済み）
1. **cashierのログイン欄の name はダミー**。メール欄もパスワード欄も両方
   `name="hogehogehogehoge"`、`id` は `_r_1_` 等でReactが毎回作り直す。
   → `input[type="text"]` / `input[type="password"]` / `button[type="submit"]`
   で指定するしかない（`etl/cashier_fetch.py` はそうしている）。
2. **cashierのCSVの日付は書き方が混在**（`2026-03-20` と `2026/6/19`）。
   IMPLEMENTATION.md は「adapters.pyが吸収済み」と書くが、pandas 2.x では
   実際は例外で落ちる。→ `etl/rows.py` で `format="mixed"` にそろえてから
   adapters に渡している（対処済み）。
3. **時刻はJST厳守**。GitHub ActionsはUTCで動く。前日計算・cronは
   `etl/settings.py` と ワークフローで明示的にJST化している。

## 二重取り込み防止（設計の要）
- 守り①: `ingest_log` にその営業日の success があれば取得せずNG
  （`rejected_duplicate`）として記録して終了。`--force` で解除。
- 守り②: `sales`/`visits` に一意キー。書き込みは「既存は無視して追加」方式。
  一意キー: `sales`=(store_id, pos_name, tx_id, line_no) /
  `visits`=(business_date, store_id, source)。

---

## ファイル地図
```
etl/
  settings.py          設定・JST・環境変数の読み込み
  browser.py           Playwright共通。失敗時に画面をdebug/へ保存
  cashier_fetch.py     cashierログイン→明細CSV取得（★セレクタ要確定）
  digitel_fetch.py     デジテールログイン→来店数CSV取得（仕様確定済み）
  rows.py              CSV→Supabase行へ変換（adapters.pyを再利用）
  supabase_client.py   Supabaseへの読み書き（重複無視の追加）
  run_daily.py         ★本体。python -m etl.run_daily
sql/
  001_schema.sql       stores/sales/visits/ingest_log ＋参考ビュー
  002_seed_stores.sql  店舗マスタ初期データ
tools/
  inspect_cashier.py   cashier画面調査ツール（ユーザーが手元で実行）
.github/workflows/
  daily-etl.yml        毎朝6時JSTのcron ＋ 手動実行
README.md              日本語の運用手順（Secrets登録・手動実行）
```

## スマホから続きを頼むときのメッセージ例
> NOTIME日次ETLの続き。まず HANDOFF.md と README.md を読んで状況を把握して。
> そのあと「次にやること」の★1と★2を一緒に進めたい。

---

## 会話の作法（ユーザーShoさんについて）
- 非エンジニア。専門用語を避けた日本語で説明する。
- 途中で細かく許可を求めない。自分で判断して進め、決めたことを事後報告する。
  質問は「ユーザーしか持っていない情報」が要るときだけ（ログイン情報・実店舗の運用実態など）。
- 認証情報の入力だけは代行しない（1回だけ理由を説明し、ユーザー作業が最小になるよう設計する）。
