# NOTIME 日次ETL（cashier 売上 / デジテール 来店数 → Supabase）

毎朝6時（日本時間）に、**前日ぶん**のデータを自動で集めて Supabase に保存します。

| 集めるもの | どこから | 入る先のテーブル |
|---|---|---|
| 売上の明細（1商品＝1行） | cashier `https://cashier.jp/v2/client/trade` | `sales` |
| 店舗の来店（入店）数 | デジテール `https://dashboard.digitail-tech.com/` | `visits` |
| 実行の記録（成功・失敗・件数） | — | `ingest_log` |
| 店舗の一覧 | — | `stores` |

どちらのサイトも「ブラウザでログインしないとCSVが取れない」ため、
Python から **Playwright**（画面を出さないChrome）を動かして取得しています。

---

## 1. はじめの準備（最初の1回だけ）

### 手順 1-1. Supabase に表（テーブル）を作る

1. Supabase の管理画面を開く → 左メニューの **SQL Editor** →「New query」
2. **`sql/setup_all.sql`** の中身を全部コピーして貼り付け、**Run**（これ1回だけ）

> `setup_all.sql` は `001_schema.sql` と `002_seed_stores.sql` を1枚にまとめたものです。
> （分けて実行したい場合は 001 → 002 の順でもOK）
> 何度実行しても壊れません。表がすでにあれば何も起きません。

### 手順 1-2. Supabase の「キー」を控える

Supabase 管理画面 → **Project Settings** → **API Keys** →
**`service_role`** の長い文字列をコピーします。

> ⚠️ このキーは全データを読み書きできる強い鍵です。
> メールやチャットに貼らず、次の手順で GitHub の Secrets にだけ登録してください。

### 手順 1-3. GitHub に Secrets（秘密の設定）を登録する

GitHub のこのリポジトリのページで
**Settings** → 左メニュー **Secrets and variables** → **Actions** →
**New repository secret** を押し、次の6つを1つずつ登録します。

| Name（名前・そのまま入力） | Secret（中身） |
|---|---|
| `CASHIER_ID` | cashier のログイン用メールアドレス（`admin@avend.co.jp`） |
| `CASHIER_PW` | cashier のパスワード |
| `DIGITAIL_ID` | デジテールの**ユーザー名**（メールアドレスではありません） |
| `DIGITAIL_PW` | デジテールのパスワード |
| `SUPABASE_URL` | `https://rwfcsanmqvkxiuwdeddv.supabase.co` |
| `SUPABASE_SERVICE_KEY` | 手順1-2でコピーした `service_role` の文字列 |

> Secrets に入れた値は、登録した本人でも後から中身を見ることはできません。
> 打ち間違えたときは同じ名前で登録し直せば上書きされます。

これで完了です。翌朝6時から自動で動き始めます。

---

## 2. 手動で動かしたいとき

### 2-1. GitHub の画面から（おすすめ・準備不要）

1. リポジトリの **Actions** タブを開く
2. 左の一覧から **NOTIME 日次ETL** を選ぶ
3. 右上の **Run workflow** を押す
4. 必要なら次を入力して **Run workflow**

| 入力欄 | 意味 | 空のままだと |
|---|---|---|
| date | 取り込みたい営業日（例 `2026-07-20`） | 前日 |
| only | `cashier` か `digitel` だけ動かす | 両方 |
| force | 取り込み済みの日をやり直す | やり直さない |

実行の様子はその場で見られます。失敗した場合は、
ページ下の **Artifacts** から `debug-…` をダウンロードすると
そのときの画面の写真が入っています。

### 2-2. 自分のパソコンから

```bash
pip install -r requirements.txt
```

```bash
python -m playwright install chromium
```

つぎに `.env.example` をコピーして `.env` を作り、ID・パスワード・キーを書きます。

```bash
Copy-Item .env.example .env
```

あとは実行するだけです。

```bash
python -m etl.run_daily
```

| やりたいこと | コマンド |
|---|---|
| 前日ぶんを取り込む | `python -m etl.run_daily` |
| 特定の日を取り込む | `python -m etl.run_daily --date 2026-07-20` |
| 来店数だけ取り込む | `python -m etl.run_daily --only digitel` |
| 取り込み済みの日をやり直す | `python -m etl.run_daily --force` |
| ブラウザの動きを目で見る | `python -m etl.run_daily --headed` |

> `.env` は `.gitignore` に入っているので、GitHub には絶対に上がりません。

---

## 3. 同じデータが二重に入らない仕組み

「毎日自動で動く」ものは、**同じ日を二度取り込んでしまう事故**が一番怖いので、
二重の守りを入れてあります。

**守り① 同じ営業日は取りに行かせない**

`ingest_log` に「その日は成功済み」という記録があると、
ETLは取得を始めずに **NG（`rejected_duplicate`）として記録して終了** します。
やり直したいときだけ `--force` を付けます。

**守り② 万一流し込んでも行は増えない**

`sales` と `visits` には「これが同じなら同じ行」という一意キーがあり、
書き込みは「既にある行は無視して追加」という方式です。

| テーブル | 一意キー |
|---|---|
| `sales` | 店舗・レジ名・伝票番号・伝票内の明細の並び順 |
| `visits` | 営業日・店舗・取得元 |

---

## 4. 実行の記録を見る（`ingest_log`）

Supabase の **Table Editor** → `ingest_log` で、毎日の結果を確認できます。

| status | 意味 |
|---|---|
| `success` | 正常に取り込めた |
| `no_data` | 取りに行けたが対象日のデータが0件（定休日など） |
| `rejected_duplicate` | 取り込み済みの日をもう一度取り込もうとした（NG） |
| `failed` | 失敗した。`message` 欄に原因が書かれています |

直近の状況を見る SQL の例：

```sql
select business_date, source, status, rows_inserted, rows_duplicate, message
from ingest_log
order by finished_at desc
limit 30;
```

---

## 5. ファイルの説明

```
etl/
  settings.py          設定（URL・日本時間・環境変数の読み込み）
  browser.py           Playwright の共通部分。失敗時に画面を保存
  cashier_fetch.py     cashier にログイン → 明細CSVをダウンロード
  digitel_fetch.py     デジテールにログイン → 来店数CSVをダウンロード
  rows.py              CSV → Supabaseに入れる形へ変換
  supabase_client.py   Supabase への読み書き（重複無視の追加）
  run_daily.py         ★毎日動く本体。ここから全部が始まる
sql/
  001_schema.sql       テーブルを作るSQL
  002_seed_stores.sql  店舗マスタの初期データ
tools/
  inspect_cashier.py   cashier の画面を調べる道具（下記6章）
.github/workflows/
  daily-etl.yml        毎朝6時に動かす設定
```

既存の `config.py` / `adapters.py` / `transform.py` / `db.py` は**変更していません**。
売上CSVの読み解き（セット割の判定や店舗名の表記ゆれ吸収）は、
検証済みの `adapters.py` をそのまま呼んで使っています。

---

## 6. cashier の画面が変わって失敗するようになったら

cashier の「期間の入力欄」と「CSVダウンロードボタン」は、
ログイン後の画面にあるため、あらかじめ形を確定できていません。
うまく動かないときは、目印（セレクタ）を確定させる必要があります。

### 6-1. いちばんラク（スマホでOK・PCログイン不要）★おすすめ

**GitHub Actions を1回動かすだけ**で、目印を確定する材料が手に入ります。
Actions は登録済みの Secrets で**本物のcashier画面にログイン**し、
もし目印が見つからなければ、その瞬間の画面の
**入力欄・ボタンの一覧と通信の記録**を自動で保存してくれます。

1. **Actions** タブ →「NOTIME 日次ETL」→ **Run workflow**（`only` は `cashier` でOK）
2. 実行が赤くなったら、ページ下の **Artifacts** から `debug-…` をダウンロード
3. その中の **`cashier_daterange_notfound_report.txt`**
   （または `cashier_csv_button_notfound_report.txt`）を開く
4. その中身をこちら（担当者・Claude）に共有 → 目印を直します

> このレポートには入力欄・ボタンの目印と、
> CSVを取りに行くときのURLが載っています。パスワードは含まれません。

### 6-2. 手元PCで調べる（従来の方法）

PCが使える場合は、次でも同じ材料が取れます。

```bash
python tools/inspect_cashier.py
```

ブラウザの窓が開くので、**ご自分で**ログインして取引一覧を表示し、
黒い画面で Enter を押してください。
`debug/cashier_report.txt` に入力欄とボタンの一覧が保存されるので、
その中身を共有していただければ、目印を直せます。

> このスクリプトはパスワードを一切保存・送信しません。

急ぎのときは、`.env` に次のように目印を直接書いても直せます。

```
CASHIER_DATE_FROM_SELECTOR=（開始日の入力欄）
CASHIER_DATE_TO_SELECTOR=（終了日の入力欄）
CASHIER_CSV_SELECTOR=（CSVボタン）
```

---

## 7. 困ったときのチェックリスト

| 症状 | 見るところ |
|---|---|
| Actions が赤くなる | Actions タブ → 失敗した実行 → ログの最後 |
| 「ログインできませんでした」 | Secrets の打ち間違い。ご自分のブラウザで入れるか確認 |
| 「テーブルが無い」系のエラー | `sql/001_schema.sql` を実行したか確認 |
| データが増えない | `ingest_log` の `status` を確認（`rejected_duplicate` なら正常な拒否） |
| 画面の形が変わった | `debug/` の Artifact をダウンロードして写真を確認 |
