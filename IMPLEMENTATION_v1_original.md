# NOTIME 日次KPI自動化 — 実装指示書（Playwright前提 / マルチPOS対応）

Claude Code にこのファイル一式を渡して実装する。

| ファイル | 役割 | 状態 |
|---|---|---|
| `config.py` | **店舗・POS構成の設定** ← 新店舗はここに足すだけ | 完成 |
| `adapters.py` | POSごとのCSV形式を共通スキーマに変換 | cashier完成 / Air・EZは要調整 |
| `transform.py` | 突合・按分・KPI算出・課題診断 | **完成（検証済み・変更不要）** |

**API・自動CSV送信は無い前提。全ソースPlaywrightでスクレイピングする。**

---

## 1. 【設計の核心】店舗を後から足せる構造

### 1店舗 = 複数POS を許容
下北沢は **Airレジ + EZレジの2レジ**。売上は合算して1店舗として扱う（内訳も出せる）。

### 入店データが無い店舗を許容
下北沢は入店カウンターが無い → **来店客数が取れない**。
このとき **購買率・来店単価は None**（クラッシュしない）。売上・客単価・商品単価は通常通り出る。
診断も自動で「来店客数」の代わりに「購入客数」で分解にフォールバックする。

### 新しい店舗の足し方
`config.py` の `STORES` に1エントリ追加するだけ。**ロジック側は一切触らない。**

```python
StoreConfig(
    name="下北沢",
    open_date="2026-01-01",
    pos_list=[
        PosConfig(name="AirREGI", adapter="airregi", store_label="下北沢"),
        PosConfig(name="EZREGI",  adapter="ezregi",  store_label="下北沢"),
    ],
    has_entry_data=False,          # 入店データ無し
    entry_source=None,
    category_override={},          # 店舗固有のカテゴリ読み替え（福井の「Tシャツ」=ラグ 等）
)
```

### 新しいPOSの足し方
`adapters.py` に `adapt_xxx()` を書いて `ADAPTERS` に登録するだけ。
共通スキーマに変換すれば、以降のロジックはPOSの違いを一切気にしない。

---

## 2. 【要対応】Airレジ・EZレジのアダプタ

`adapters.py` の `adapt_airregi()` / `adapt_ezregi()` は **カラム名が仮置き**。
**実CSVを1本ずつ取得して、以下を確定させること:**

1. **カラム名**（取引ID / 日付 / カテゴリ / 単価 / 数量 / 金額）
2. **金額が税込か税抜か** ← `AMOUNT_IS_TAX_INCLUDED` フラグを正しく設定。
   ⚠️ ここを間違えると10%ズレたまま気づかない。**最重要の確認項目。**
3. **取引ID（伝票番号）があるか** ← 無いと「購入客数」が数えられない。
   無い場合は「取引日時＋レシート番号」等で代替キーを作る。
4. **バンドル（セット割）機能を使っているか** ← 使っていれば cashier 同様の按分が必要。
   現状は「使っていない」前提で全て通常明細として扱っている。
5. **カテゴリ名がcashierと揃っているか** ← 揃っていなければ `category_override` でマッピング。

---

## 3. データソース

| ソース | URL | 認証 | 対象店舗 |
|---|---|---|---|
| デジテール（入店） | `https://dashboard.digitail-tech.com/auth/login` | ID/PW | 山形・いわき・福井 |
| cashier（POS） | `https://cashier.jp` | ID/PW | 山形・いわき・福井 |
| Airレジ（POS） | Web管理画面 | ID/PW | 下北沢 |
| EZレジ（POS） | Web管理画面 | ID/PW | 下北沢 |

**取得は毎日 / Slack通知は月木**。
（cashierは当日データが未確定で後から増えるため、毎日取り込んで前日分を確定させる必要がある）

---

## 4. 【最重要】スクレイピングは必ず壊れる。壊れる前提で作る

### 4-1. 生CSVを必ずアーカイブ（最優先）
加工前の生データを日付付きで保存する。

```
raw/
  digitail/2026-07-13_山形.csv
  cashier/2026-07-13_TradeDetail.csv
  airregi/2026-07-13_下北沢.csv
  ezregi/2026-07-13_下北沢.csv
```

**理由**: スクレイパーが壊れて数日気づかなくても、生データがあれば復元できる。
捨てているとその期間は**永久に欠損**する。ここをケチると詰む。

### 4-2. 失敗は必ずSlackに叫ぶ
黙って死ぬのが最悪。以下は即アラート:
- ログイン失敗 / セレクタが見つからない
- CSVが空、想定カラムが無い
- 取得データに「前日」が含まれていない
- 行数が前回比で急減（通常の30%未満）

### 4-3. スモークテスト（書き込み前の自己検証）
通らなければ**書き込まずにアラート**。壊れたデータで上書きするのが最悪の事故。

### 4-4. リトライ
3回リトライ（exponential backoff）してから諦める。

### 4-5. 冪等性
スプシは**追記でなく全書き換え**。「何度実行しても同じ結果」が絶対条件。

---

## 5. Playwright実装のポイント

### まずXHR直叩きを試す（ブラウザ操作を最小化）
CSVダウンロードボタンは内部で必ずAPIを叩いている。
DevToolsのNetworkタブでエンドポイントを特定し、
**ログイン後のセッションCookieで `requests` から直接叩けないか試す。**

```python
context = browser.new_context()
page = context.new_page()
page.goto(LOGIN_URL)
page.fill('input[name="email"]', ID)
page.fill('input[name="password"]', PW)
page.click('button[type="submit"]')
page.wait_for_url("**/dashboard**")

session = requests.Session()
for c in context.cookies():
    session.cookies.set(c["name"], c["value"])
csv_text = session.get(CSV_ENDPOINT, params={...}).text
```

これができればUI変更の影響をほぼ受けない。**必ず先に試す。**
ダメならフォールバックで `page.expect_download()` を使う。

### セレクタは意味依存で書く
- ❌ `div > div:nth-child(3) > button`（すぐ壊れる）
- ⭕️ `text=CSVダウンロード` / `[data-testid=...]` / `input[name="email"]`

---

## 6. 【必読】実運用で踏んだ落とし穴（transform.pyに実装済み）

### 6-1. 未確定日は必ず捨てる
エクスポート当日のデータは未確定。実測:

| 日付 | 取得時点 | 福井の伝票数 |
|---|---|---|
| 6/20 | 6/20 17:05 | 72件（未確定） |
| 6/20 | 6/22 09:34 | **113件（確定）** |
| 6/22 | 6/22 09:34 | 5件（未確定） |
| 6/22 | 6/24 15:41 | **46件（確定）** |

→ **CUTOFF = 実行日の前日**。

### 6-2. 過去日も後から増える → 常に最新で上書き
「一度取り込んだ日は触らない」はNG。`merge_layers()` で後勝ち上書き。

### 6-3. 日付形式の揺れ
cashierは `2026-03-20` と `2026/6/19` の**両方**を返す。（吸収済み）

### 6-4. 店舗名の表記ゆれ
`NOTIMEいわき（2026/4/25~2026/4/28)` のような括弧付きが混じる。（正規化済み）

### 6-5. バンドル（セット割）の按分
POSはセット割の親行を「セール商品」に計上する。**売上の20%超**がここに埋もれる。
構成品の販売価格比で実カテゴリへ按分しないと商品分析が壊れる。（実装済み）

### 6-6. 税抜で統一
売上・客単価・商品単価はすべて**税抜**。「純売上」＝税抜、「総売上」＝税込。混ぜない。

### 6-7. カテゴリの店舗差
- **Tシャツは新分類（無地/プリント/バンド/アニメ）のまま保持。統合しない。**
- **福井のみ「Tシャツ」タグ＝ラグ**として読み替え（`category_override`）。
- レジ袋・クーポンは物販から除外。

---

## 7. スプレッドシート出力

**既存のスプシに新規シートとして追加**（新規スプシは作らない）。
`gspread` + サービスアカウント。**対象スプシをサービスアカウントのメールに「編集者」で共有**すること。

| シート名 | 内容 |
|---|---|
| `日次KPI_{店舗}` | date / 曜日 / 来店客数 / 購入客数 / 購買率 / 購入点数 / 売上税抜 / 売上税込 / 客単価 / 平均購入数 / 商品単価 / 来店単価 |
| `レジ別_{店舗}` | POS別の売上・購入客数・構成比（**下北沢のような複数レジ店舗用**） |
| `カテゴリ日次_{店舗}` | date × カテゴリ の販売数マトリクス |
| `価格帯_{店舗}` | 価格帯別 売上・販売数・構成比 |
| `診断ログ` | 実行日 / 店舗 / ボトルネック / 各KPI前週比 |

シート名は `config.STORES` をループして自動生成する（**新店舗追加時に自動で増える**）。
入店データが無い店舗は、購買率・来店単価の列を空欄にする。

---

## 8. Slack通知（月・木）

```python
if datetime.now().weekday() in (0, 3):   # 月=0, 木=3
    push_slack()
```

```
📊 NOTIME KPI（{期間}）

【福井】日商 ¥XXX,XXX（前週比 +X%）
  来店 XXX/日 ・ 購買率 XX.X% ・ 客単価 ¥X,XXX ・ 商品単価 ¥X,XXX
  ⚠️ ボトルネック: 来店客数（前週比 -XX%）

【下北沢】日商 ¥XXX,XXX（前週比 +X%）  ※入店データ無し
  購入 XX人/日 ・ 客単価 ¥X,XXX ・ 商品単価 ¥X,XXX
  レジ内訳: Airレジ ¥XXX,XXX / EZレジ ¥XXX,XXX

💡 示唆
（Claude APIが生成）
```

### 示唆生成プロンプト（Claude API / claude-sonnet-5）

```
あなたはアパレル小売のデータアナリストです。
NOTIME（古着チェーン）のKPIを分析し、課題を特定してください。

【前提】
- 売上 = 来店客数 × 購買率 × 平均購入数 × 商品単価
- 日次売上との相関は「来店客数」が圧倒的（Spearman 0.87〜0.92）。
  購買率・客単価・商品単価の相関は低い（0.1〜0.4）。
  → 売上の振れは基本的に「集客」で決まる。
- 営業利益率50%が目標。安易な値引きは粗利を壊すので推奨しない。
- 価格帯は ¥2,980〜3,980 が数量の主戦場、¥5,980以上が単価源。
- 一部店舗（下北沢）は入店データが無く、購買率・来店単価は算出不能。
  その店舗は購入客数ベースで評価すること。

【データ】
{各店のKPI・前週比・寄与度・ボトルネック}
{カテゴリ別 売上/販売数 構成比}
{価格帯別 売上/販売数 構成比}
{複数レジ店舗はレジ別内訳}

【出力】
1. 各店の最大のボトルネックはどのKPIか
2. カテゴリ・価格帯の観点で、売れ筋の変化や在庫の偏りはないか
3. 具体的な打ち手（値引き以外を優先）
簡潔に、経営会議で使える粒度で。
```

---

## 9. GitHub Actions

```yaml
name: notime-daily
on:
  schedule:
    - cron: '0 23 * * *'   # UTC23:00 = JST 8:00
  workflow_dispatch:

permissions:
  contents: write          # raw/ をコミットするため

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt && playwright install --with-deps chromium
      - run: python main.py
        env:
          DIGITAIL_ID:       ${{ secrets.DIGITAIL_ID }}
          DIGITAIL_PW:       ${{ secrets.DIGITAIL_PW }}
          CASHIER_ID:        ${{ secrets.CASHIER_ID }}
          CASHIER_PW:        ${{ secrets.CASHIER_PW }}
          AIRREGI_ID:        ${{ secrets.AIRREGI_ID }}
          AIRREGI_PW:        ${{ secrets.AIRREGI_PW }}
          EZREGI_ID:         ${{ secrets.EZREGI_ID }}
          EZREGI_PW:         ${{ secrets.EZREGI_PW }}
          GCP_SA_JSON:       ${{ secrets.GCP_SA_JSON }}
          SPREADSHEET_ID:    ${{ secrets.SPREADSHEET_ID }}
          SLACK_WEBHOOK:     ${{ secrets.SLACK_WEBHOOK }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - run: |
          git config user.name "notime-bot"
          git config user.email "bot@notime"
          git add raw/
          git diff --staged --quiet || git commit -m "raw data $(date +%F)"
          git push
```

**認証情報は絶対にコードに書かない。すべてGitHub Secrets。**

---

## 10. requirements.txt

```
playwright>=1.44
pandas>=2.2
numpy>=1.26
gspread>=6.0
google-auth>=2.29
anthropic>=0.34
requests>=2.32
```

---

## 11. 実装の順序

1. **Airレジ・EZレジの実CSVを1本ずつ取得** → `adapters.py` のカラム名を確定（§2）。
   特に**税込/税抜の判定**は必ず実データで検証すること。
2. `fetch_digitail.py` / `fetch_cashier.py` / `fetch_airregi.py` / `fetch_ezregi.py` を実装。
   まず**XHR直叩き**を試す（§5）。
3. `transform.py` はそのまま使う（**完成済み・変更不要**）。
4. `push_sheets.py` → `push_slack.py`。
5. **エラー通知・スモークテスト・生CSVアーカイブを必ず入れる**（§4）。
6. ローカルで手動実行して検証してからActionsに載せる。

### 検証方法（正解データ）
6/23時点の集計が以下と一致すればOK（**リファクタ後も一致確認済み**）:

| 店舗 | 売上税抜 | 購買率 | 客単価 |
|---|---|---|---|
| 山形 | ¥13,825,236 | — | ¥6,871 |
| いわき | ¥9,364,129 | 24.6% | ¥6,703 |
| 福井 | ¥7,234,488 | 29.4% | ¥7,227 |

---

## 12. 診断の観点

```
売上 = 来店客数 × 購買率 × 平均購入数 × 商品単価
                └─ 購入客数 ─┘   └──── 客単価 ────┘
```
（入店データ無しの店舗は「購入客数 × 平均購入数 × 商品単価」で分解）

- **来店客数**: 最重要。売上との相関0.9。落ちていれば集客施策が答え。
- **購買率**: 来店の質。落ちていれば品揃え・在庫薄・店内環境を疑う。
- **平均購入数**: セット販売・アタッチ販売の効き具合。小物はバンドル同梱で売れる。
- **商品単価**: 季節要因が大きい（夏＝Tシャツ中心で下がる／冬＝アウターで上がる）。
- **カテゴリ構成**: 売上比率 vs 販売数比率のズレ。
  - 売上比率 > 販売数比率 → 単価ドライバー（アウター・デニム）
  - 販売数比率 > 売上比率 → ボリュームドライバー（半袖・Tシャツ）
- **価格帯**: ¥2,980〜3,980（数量の主戦場）と ¥5,980以上（単価源）の二層バランス。
