# notime-property-scout

NOTIME / SELFURUGI 出店候補物件の日次自動収集と優先順位付け（仕様書 Phase 3）。
本リポジトリは **MVP（§11）** の実装。足切りと優先順位付けまでを担い、**合格判定は出さない**（§1.3 / §13）。

## MVPのスコープ（§11）

- ソース: **不動産連合隊（fudosanlist.cbiz.ne.jp）のみ** × 6市（`cities.yaml`）
- ゲート条件をURLパラメータで表現（§5.1.1）→ 取得段階でノイズを落とす
- 面積・家賃・駐車場・階数・坪単価で **ゲート（§2）＋採点（§3）**（§7の補強なし）
- **SQLite 保存＋差分検知（§6）**
- **HTMLレポート（§9.2）＋TSV出力（§9.3）**（通知は入れない）

未実装（第2段階以降）: OSM接道判定（§7.1）、Places（§7.2）、Street View（§7.3）、
発見エンジン（§5.2）、AtHome半自動取り込み（§5.3）、Slack/Supabase。

### MVPでの割り切り（仕様どおり）

- **G4（幹線道路から50m以内）** は OSM 未実装のため、掲載元の「路面店/幹線道路沿い」
  フラグで暫定判定。フラグが無ければ `除外` ではなく `要確認`（§2.2 の取りこぼし防止）。
- **AI評価（§3.3）** は接道・Placesが無いため **全件「判定不可」**。ロジックの器と
  立地パターン辞書（`src/location.py`）は実装済みで、第2段階でデータが埋まれば自動で判定が走る。
- **面積の単位が特定できなければパースを失敗させる**（§9.1.1 / §13）。推測で埋めない。

## 実行環境（§10・重要）

**手元Mac または小規模VPS で実行する。** データセンターIP（GitHub Actions / クラウド)は
連合隊にボット判定で遮断されるため、取得はローカルから行うこと。

```bash
pip install -r requirements.txt
cp .env.example .env   # 連絡先などを設定（§5.4 UAに連絡先を明記）
```

## コマンド

```bash
# 一覧URLを組んで表示（取得しない。§5.1.1 のパラメータ確認用）
python -m src.cli url --city 大分市

# 手元で1回だけ一覧を取得して samples/ に保存（robots尊重・3秒間隔・§5.4）
python -m src.cli fetch --city 大分市 --out samples/oita.html

# 保存したHTMLをパースし、坪数/家賃/駐車場/階数/路面フラグ/判定を表示
python -m src.cli parse-file samples/oita.html --city 大分市

# 日次バッチ（全市 or 指定市）。SQLite保存→差分検知→out/ にHTML+TSV
python -m src.cli run                 # 全6市
python -m src.cli run --priority 1    # priority:1 の4市のみ（§6.0 推奨の着手順）
python -m src.cli run --city 大分市

# ネット不要の実証（面積パーサ/ゲート/スコア/立地/出力を合成データで確認）
python -m src.cli selftest
```

### パーサを本番投入する前に（§5.2「人のレビュー（必須）」）

連合隊のURL実キー名とカードのCSSセレクタは **実ページを1回取得して確定** させること。
`registry.yaml` の連合隊エントリは `status: pending`。`fetch` → `parse-file` で
坪数・家賃・駐車場・階数が正しく取れることを目視確認してから `verified` にする。
（パーサは `src/sources/rengotai.py`。セレクタが外れても本文正規表現で拾う二段構え。）

## 構成（§10）

```
notime-property-scout/
├─ src/
│  ├─ sources/rengotai.py   # §5.1.1 連合隊のURL構築＋パーサ
│  ├─ area.py               # §9.1.1 面積の厳格パース（単位不明は失敗）
│  ├─ normalize.py          # RawListing → Property（税込換算・取得費算出）
│  ├─ gates.py              # §2 必須ゲート
│  ├─ scoring.py            # §3 スコア／§3.1 想定月商／§3.4 ランク
│  ├─ location.py           # §3.3 立地類似判定（器＋パターン辞書。MVPは判定不可）
│  ├─ db.py                 # §10 SQLite 保存＋§6 差分検知
│  ├─ http.py               # §5.4 収集マナー（robots/3秒/クールダウン）
│  ├─ reporters/            # §9 html.py / tsv.py（アダプタ方式）
│  ├─ run.py / cli.py       # §11 日次バッチ
│  └─ selftest.py           # ネット不要の実証
├─ cities.yaml              # §6.0 対象6市
├─ registry.yaml            # §5.2 ソースレジストリ（連合隊=pending）
├─ data/properties.db       # SQLite（.gitignore）
└─ out/                     # 日次レポート（.gitignore）
```

## 出力（§9.1・9列共通）

見つけた日 / 物件名 / 坪数（坪主・㎡併記）/ 家賃（税込）/ 駐車場台数 /
主要駅からの距離（MVPは要確認）/ 物件のリンク（個別URL）/ Googleマップ（住所文字列）/ AI評価。
内部スコア・ランクは並び順にのみ使い、列には出さない（HTMLの折りたたみ内に補助表示）。
