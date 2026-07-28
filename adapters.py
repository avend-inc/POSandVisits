"""
POSアダプタ層

【役割】
POSごとにCSVの形式がバラバラ（cashier / Airレジ / EZレジ …）。
それを **共通スキーマ** に変換するのがアダプタの仕事。
これ以降のロジック（transform.py）は、どのPOSから来たか一切気にしない。

【共通スキーマ】必須カラム
  date          : YYYY-MM-DD
  ts            : YYYY-MM-DD HH:MM:SS（時間帯別分析に使う。取れなければ日付00:00）
  tx_id         : 伝票（取引）ID  ※POS間で衝突しないよう pos_name をprefixする
  pos_name      : どのレジか（"cashier" / "AirREGI" / "EZREGI"）
  store         : 店舗名（"山形" "下北沢" …）
  sales_ex_tax  : 伝票の売上（税抜）  ※伝票ヘッダ値。明細行に同じ値が繰り返される
  sales_in_tax  : 伝票の売上（税込）
  tx_qty        : 伝票の購入点数
  line_category : 明細の商品カテゴリ名
  line_price    : 明細の販売価格（単価）
  line_qty      : 明細の商品点数
  line_amount   : 明細の商品小計金額
  bundle_code   : バンドル（セット割）コード。無ければ ""
  is_parent     : バンドル親行か
  is_child      : バンドル子行か
  is_normal     : 通常明細か

【新しいPOSを足すには】
  1. def adapt_xxx(df) を書く（共通スキーマに変換）
  2. ADAPTERS に登録
  3. config.py の PosConfig(adapter="xxx") で指定
  それだけ。transform.py は触らない。
"""
import pandas as pd

COMMON_COLUMNS = [
    "date", "ts", "tx_id", "pos_name", "store",
    "sales_ex_tax", "sales_in_tax", "tx_qty",
    "line_category", "line_price", "line_qty", "line_amount",
    "bundle_code", "is_parent", "is_child", "is_normal",
]


def _num(s):
    # ¥ や カンマ付き（"¥7,980" / "7,980"）でも数値化できるようにする。
    return pd.to_numeric(
        s.astype(str).str.replace(r"[¥￥,\s]", "", regex=True).replace({"": None, "nan": None}),
        errors="coerce",
    ).fillna(0)


def _normalize_store_name(s: pd.Series) -> pd.Series:
    """
    店舗名の正規化。
    "NOTIMEいわき（2026/4/25~2026/4/28)" → "いわき"
    括弧付きの表記ゆれが実際に混じっていたので必須。
    """
    return (
        s.astype(str)
        .str.replace(r"[（(].*", "", regex=True)
        .str.replace("NOTIME", "", regex=False)
        .str.strip()
    )


# ============================================================
# cashier（山形・いわき・福井）
# ============================================================
def adapt_cashier(df: pd.DataFrame, pos_name: str) -> pd.DataFrame:
    """cashier TradeDetail 形式。伝票ヘッダ+明細がフラットに繰り返される。"""
    df = df.copy().fillna("")
    out = pd.DataFrame()

    # 日付形式の揺れ: "2026-03-20" と "2026/6/19" の両方が来る
    out["date"] = pd.to_datetime(df["伝票：精算日付（締日）"]).dt.strftime("%Y-%m-%d")
    out["ts"] = pd.to_datetime(df["伝票：処理日時"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    out["ts"] = out["ts"].fillna(out["date"] + " 00:00:00")
    # 伝票IDはPOS間で衝突しうるので prefix する
    out["tx_id"] = pos_name + ":" + df["伝票：取引番号"].astype(str)
    out["pos_name"] = pos_name
    out["store"] = _normalize_store_name(df["伝票：店舗名"])

    out["sales_ex_tax"] = _num(df["伝票：小計金額"])
    out["sales_in_tax"] = _num(df["伝票：合計金額"])
    out["tx_qty"] = _num(df["伝票：購入点数"])

    out["line_category"] = df["明細：商品カテゴリ名"]
    out["line_price"] = _num(df["明細：販売価格"])
    out["line_qty"] = _num(df["明細：商品点数"])
    out["line_amount"] = _num(df["明細：商品小計金額"])

    # バンドル（セット割）構造
    bundle = df["明細：商品バンドルコード"].astype(str).replace("nan", "")
    out["bundle_code"] = bundle
    out["is_parent"] = (df["明細：商品コード１"].astype(str) == bundle) & (bundle != "")
    out["is_child"] = (bundle != "") & (~out["is_parent"])
    out["is_normal"] = bundle == ""
    return out[COMMON_COLUMNS]


# ============================================================
# Airレジ（下北沢）— 会計明細CSV
# ============================================================
# 【実CSVで確定した仕様】
#  - 文字コード: Shift-JIS (cp932)
#  - 1行 = 1明細。取引Noで伝票がまとまる
#  - 「小計」= 伝票の税抜合計 / 「合計」= 税込（ただし丸めで数円ズレる）
#  - 「価格」= 商品の税抜単価（個別割引がある場合は小計と一致しない）
#  - 「内消費税」あり
#  - まとめ買い割引(バンドル)は 未使用（実データで0件）→ 全て通常明細
#  - カテゴリ名がcashierと異なる → AIRREGI_CATEGORY_MAP でマッピング
AIRREGI_CATEGORY_MAP = {
    "Tシャツ": "Tシャツ",
    "アニメT": "アニメ/ミュージックTシャツ",
    "バンドT": "バンド/ミュージックTシャツ",
    "ディズニーT": "プリントTシャツ",
    "ムービーT": "プリントTシャツ",
    "ハーレー": "プリントTシャツ",
    "薄アウター": "アウター/ジャケット",
    "中間アウター": "アウター/ジャケット",
    "半袖シャツ": "半袖シャツ",
    "長袖シャツ": "長袖シャツ",
    "ポロシャツ": "ポロシャツ",
    "スウェット": "スウェット/パーカー",
    "デニム": "デニム",
    "チノ/スラックスなど": "チノ/スラックス等",
    "小物": "小物",
    "特価": "小物",
    "レジ袋": "レジ袋",      # 物販から除外される
}


def adapt_airregi(df: pd.DataFrame, pos_name: str, store: str) -> pd.DataFrame:
    """Airレジ 会計明細CSV → 共通スキーマ。読み込みは encoding='cp932'。"""
    df = df.copy()

    # ⚠️【重要】複数商品の取引では、2行目以降のヘッダ列が空欄になる形式。
    #   取引Noは全行に入っているので、それでグループ化して前方補完する。
    #   これをやらないと明細の37%が日付なしで捨てられる（実データで確認済み）。
    HEADER_COLS = ["会計日", "会計時間", "来店日", "来店時間",
                   "合計", "小計", "内消費税", "商品点数", "人数"]
    for c in HEADER_COLS:
        if c in df.columns:
            df[c] = df[c].replace("", pd.NA)
            df[c] = df.groupby("取引No")[c].ffill()
    df = df.fillna("")

    out = pd.DataFrame()

    out["date"] = pd.to_datetime(df["会計日"]).dt.strftime("%Y-%m-%d")
    out["ts"] = pd.to_datetime(
        df["会計日"].astype(str) + " " + df["会計時間"].astype(str), errors="coerce"
    ).dt.strftime("%Y-%m-%d %H:%M:%S")
    out["ts"] = out["ts"].fillna(out["date"] + " 00:00:00")
    out["tx_id"] = pos_name + ":" + df["取引No"].astype(str)
    out["pos_name"] = pos_name
    out["store"] = store

    # 伝票ヘッダ（明細行に繰り返される）
    # 小計=税抜 / 合計=税込。丸め差があるので、税抜は小計をそのまま信頼する。
    out["sales_ex_tax"] = _num(df["小計"])
    out["sales_in_tax"] = _num(df["合計"])
    out["tx_qty"] = _num(df["商品点数"])

    # 明細
    raw_cat = df["カテゴリー名"].astype(str).str.strip('"')
    out["line_category"] = raw_cat.map(lambda c: AIRREGI_CATEGORY_MAP.get(c, "小物"))
    out["line_price"] = _num(df["価格"])          # 税抜単価
    out["line_qty"] = _num(df["注文数量"])
    # 明細金額 = 価格×数量 + 個別割引（割引はマイナス値で入る）
    out["line_amount"] = _num(df["価格"]) * _num(df["注文数量"]) + _num(df["個別割引/割増合計額"])

    # まとめ買い割引(バンドル)は未使用 → 全て通常明細
    out["bundle_code"] = ""
    out["is_parent"] = False
    out["is_child"] = False
    out["is_normal"] = True
    return out[COMMON_COLUMNS]


# ============================================================
# EZレジ（下北沢）— TranList CSV
# ============================================================
# 【実CSVで確定した仕様 / ⚠️重要な制約】
#  - 文字コード: UTF-8 (BOM付き)
#  - 1行 = 1取引（レシート単位）。**商品明細が無い**
#    → カテゴリ別・価格帯別の分析は EZレジ分については不可能
#    → 売上・購入客数・点数・客単価・商品単価 は算出できる
#  - 「合計金額」は **税込**（÷1.1 で ¥2,490/¥2,980/¥3,490/¥4,980 等の
#    NOTIME価格体系にぴったり一致することを実データで確認済み）
#  - 「取引区分」に "返品" 等が入りうる → "支払" のみを対象にする
EZREGI_TAX_RATE = 1.10


def adapt_ezregi(df: pd.DataFrame, pos_name: str, store: str | None = None) -> pd.DataFrame:
    """
    EZレジ/Si 明細CSV（【Si】DB形式）→ 共通スキーマ。

    【実データで確定した仕様（Googleスプレッドの【Si】DBシート）】
     - 1行 = 1明細。複数店舗が1ファイルに混在 → 店舗名で振り分ける（引数storeは使わない）。
     - 列: 店舗コード, 店舗名, レジNo, レシートNo, 行No, 購入日時, 商品分類名, 商品名,
           売上数, 税率(%), 本体価格, 本体合計, 税抜売価, 税込単価, 価格(税込), 税込売価,
           値引額1, セール名1, 値引額2, セール名2, 支払方法
     - 税抜売価 / 税込売価 は「値引後」の金額 → SALE値引きが反映済み。
     - 商品分類名がNOTIMEカテゴリとほぼ一致（"レジ袋" は物販から自動除外）。
     - 伝票(レシート)合計の列は無いので、レシート単位で明細を合算して作る（cashierと同形）。
    """
    df = df.copy().fillna("")
    # 店舗名が空の行（集計行・空行）は捨てる
    df = df[df["店舗名"].astype(str).str.strip() != ""]

    out = pd.DataFrame()
    dt = pd.to_datetime(df["購入日時"], errors="coerce")
    out["date"] = dt.dt.strftime("%Y-%m-%d")
    out["ts"] = dt.dt.strftime("%Y-%m-%d %H:%M:%S")
    out["ts"] = out["ts"].fillna(out["date"].astype(str) + " 00:00:00")
    out["store"] = _normalize_store_name(df["店舗名"])
    # レシート = 店舗コード + レジNo + レシートNo。POS間衝突を避けるため pos_name を prefix。
    out["tx_id"] = (pos_name + ":" + df["店舗コード"].astype(str) + "-"
                    + df["レジNo"].astype(str) + "-" + df["レシートNo"].astype(str))
    out["pos_name"] = pos_name

    ex = _num(df["税抜売価"])       # 値引後・税抜（明細）
    intax = _num(df["税込売価"])    # 値引後・税込（明細）
    qty = _num(df["売上数"])
    out["line_qty"] = qty
    out["line_amount"] = ex
    out["line_price"] = _num(df["本体価格"])
    out["line_category"] = df["商品分類名"].astype(str).str.strip().replace("", "その他")

    # レシート単位の合計（税抜/税込/点数）を作り、明細行に同じ値を繰り返す。
    tmp = pd.DataFrame({"tx_id": out["tx_id"], "_ex": ex, "_in": intax, "_q": qty})
    tot = tmp.groupby("tx_id").agg(_ex=("_ex", "sum"), _in=("_in", "sum"), _q=("_q", "sum"))
    out["sales_ex_tax"] = out["tx_id"].map(tot["_ex"])
    out["sales_in_tax"] = out["tx_id"].map(tot["_in"])
    out["tx_qty"] = out["tx_id"].map(tot["_q"])

    out["bundle_code"] = ""
    out["is_parent"] = False
    out["is_child"] = False
    out["is_normal"] = True
    return out[COMMON_COLUMNS]


# ============================================================
# アダプタ登録
# ============================================================
ADAPTERS = {
    "cashier": adapt_cashier,
    "airregi": adapt_airregi,
    "ezregi": adapt_ezregi,
}


def adapt(df: pd.DataFrame, adapter_name: str, pos_name: str, store: str | None = None) -> pd.DataFrame:
    """アダプタを名前で呼び出す。"""
    fn = ADAPTERS[adapter_name]
    if adapter_name == "cashier":
        return fn(df, pos_name)          # cashierはCSVに店舗名が入っている
    return fn(df, pos_name, store)       # Air/EZは設定から店舗名を渡す
