"""
NOTIME 日次KPI集計ロジック（確定版 / マルチPOS対応）

【設計】
- POSの違いは adapters.py が吸収済み。ここは共通スキーマだけを見る。
- 1店舗に複数POS（例: 下北沢 = Airレジ + EZレジ）→ 合算して1店舗として扱う。
- 入店データが無い店舗（下北沢）→ 来店客数依存のKPIは None を返す。クラッシュしない。

【実運用で踏んだ落とし穴（実装済み）】
1. 未確定日: エクスポート当日は数字が確定していない（実測で後から1.5倍になった）
   → CUTOFF = 実行日の前日 まで
2. 過去日も後から増える → 同じ日付は常に最新の取得で上書き（merge_layers）
3. 日付形式の揺れ / 店舗名の括弧付き表記ゆれ → adapters側で正規化
4. バンドル(セット割)按分: 親行が「セール商品」に計上され売上の20%超が埋もれる
   → 構成品の販売価格比で実カテゴリへ按分
5. 税抜統一（純売上=税抜 / 総売上=税込）
"""
import pandas as pd
import numpy as np

from config import (
    StoreConfig, get_store, CATEGORIES, NON_MERCH, PRICE_BANDS, WEEKDAY_JP,
)


# ============ レイヤー結合 ============
def merge_layers(frames: list[pd.DataFrame], key_cols: list[str]) -> pd.DataFrame:
    """
    差分エクスポートのレイヤー結合。
    後のフレームほど新しい。同じキーは「後勝ち」で上書きする。

    ⚠️ 過去日ですら後から数字が増えるため、「一度取り込んだ日は触らない」はNG。
    """
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return combined.drop_duplicates(subset=key_cols, keep="last")


def normalize_entry(df: pd.DataFrame) -> pd.DataFrame:
    """デジテール入店データの正規化。"""
    df = df.iloc[:, :3].copy()
    df.columns = ["date", "来店客数", "常連来店数"]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for c in ["来店客数", "常連来店数"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    return df


# ============ カテゴリ按分 ============
def allocate_categories(pos: pd.DataFrame, store: StoreConfig) -> pd.DataFrame:
    """
    バンドル(セット割)の売上を構成品の販売価格比で実カテゴリへ按分。
    これをやらないと売上の20%超が「セール商品」に埋もれて商品分析が壊れる。
    戻り値: date / category / sales / qty
    """
    override = store.category_override

    def to_group(cat: str) -> str | None:
        cat = override.get(cat, cat)
        if cat in NON_MERCH:
            return None                      # 物販から除外
        if cat in CATEGORIES or cat in override.values():
            return cat
        return "小物"

    rows = []
    # 通常明細
    for _, r in pos[pos["is_normal"]].iterrows():
        rows.append((r["date"], r["line_category"], r["line_amount"], r["line_qty"]))

    # バンドル: 親の金額を子の販売価格比で按分
    bundled = pos[pos["bundle_code"] != ""]
    if len(bundled):
        for _, g in bundled.groupby(["tx_id", "bundle_code"]):
            amount = g[g["is_parent"]]["line_amount"].sum()
            children = g[g["is_child"]]
            date = g["date"].iloc[0]
            if len(children) == 0:
                parent = g[g["is_parent"]]
                if len(parent):
                    rows.append((date, parent.iloc[0]["line_category"], amount, 0))
                continue
            base = children["line_price"].sum()
            for _, c in children.iterrows():
                w = c["line_price"] / base if base > 0 else 1 / len(children)
                rows.append((date, c["line_category"], amount * w, c["line_qty"]))

    out = pd.DataFrame(rows, columns=["date", "raw_cat", "sales", "qty"])
    if len(out) == 0:
        return pd.DataFrame(columns=["date", "category", "sales", "qty"])
    out["category"] = out["raw_cat"].map(to_group)
    return out[out["category"].notna()].drop(columns=["raw_cat"])


def price_band(price: float) -> str:
    for lo, hi, label in PRICE_BANDS:
        if lo <= price <= hi:
            return label
    return PRICE_BANDS[-1][2]


# ============ 日次KPI ============
def build_daily_kpi(pos_all: pd.DataFrame, entry: pd.DataFrame | None,
                    store: StoreConfig, cutoff: str) -> pd.DataFrame:
    """
    日次KPIテーブル。cutoff（含む）までの確定データのみ。

    複数POSは自動で合算される（tx_id が pos_name prefix 付きなので衝突しない）。
    entry=None（入店データ無し）の場合、来店依存KPIは NaN になる。
    """
    pos = pos_all[(pos_all["store"] == store.name) & (pos_all["date"] <= cutoff)]

    # 伝票単位に集約（明細行にヘッダ値が繰り返されるため first を取る）
    tx = pos.groupby(["date", "tx_id"]).agg(
        売上税抜=("sales_ex_tax", "first"),
        売上税込=("sales_in_tax", "first"),
        点数=("tx_qty", "first"),
    ).reset_index()

    daily = tx.groupby("date").agg(
        購入客数=("tx_id", "nunique"),
        購入点数=("点数", "sum"),
        売上税抜=("売上税抜", "sum"),
        売上税込=("売上税込", "sum"),
    ).reset_index()

    # 入店データの結合（無い店舗もある）
    if store.has_entry_data and entry is not None and len(entry):
        e = entry[entry["date"] <= cutoff]
        daily = e.merge(daily, on="date", how="outer").fillna(0)
        for c in ["来店客数", "常連来店数"]:
            daily[c] = daily[c].astype(int)
    else:
        daily["来店客数"] = np.nan
        daily["常連来店数"] = np.nan

    for c in ["購入客数", "購入点数", "売上税抜", "売上税込"]:
        daily[c] = daily[c].fillna(0).astype(int)

    daily = daily[daily["date"] <= cutoff].sort_values("date").reset_index(drop=True)
    daily["曜日"] = pd.to_datetime(daily["date"]).dt.weekday.map(lambda x: WEEKDAY_JP[x])
    open_date = pd.Timestamp(store.open_date)
    daily["経過週"] = ((pd.to_datetime(daily["date"]) - open_date).dt.days // 7 + 1)

    # 派生KPI
    buyers = daily["購入客数"]
    daily["客単価"] = np.where(buyers > 0, daily["売上税抜"] / buyers, 0)
    daily["平均購入数"] = np.where(buyers > 0, daily["購入点数"] / buyers, 0)
    daily["商品単価"] = np.where(daily["購入点数"] > 0, daily["売上税抜"] / daily["購入点数"], 0)

    # 来店依存KPI（入店データが無ければ NaN のまま）
    if store.has_entry_data:
        v = daily["来店客数"]
        daily["購買率"] = np.where(v > 0, buyers / v, 0)
        daily["来店単価"] = np.where(v > 0, daily["売上税抜"] / v, 0)
    else:
        daily["購買率"] = np.nan
        daily["来店単価"] = np.nan

    return daily


def build_pos_breakdown(pos_all: pd.DataFrame, store: StoreConfig, cutoff: str) -> pd.DataFrame:
    """
    レジ別の内訳。下北沢のような複数POS店舗で「どちらのレジがいくらか」を見る用。
    単一POSの店舗でも問題なく動く（1行返る）。
    """
    pos = pos_all[(pos_all["store"] == store.name) & (pos_all["date"] <= cutoff)]
    tx = pos.groupby(["pos_name", "tx_id"]).agg(
        売上税抜=("sales_ex_tax", "first"),
        売上税込=("sales_in_tax", "first"),
        点数=("tx_qty", "first"),
    ).reset_index()
    g = tx.groupby("pos_name").agg(
        購入客数=("tx_id", "nunique"),
        購入点数=("点数", "sum"),
        売上税抜=("売上税抜", "sum"),
        売上税込=("売上税込", "sum"),
    ).reset_index()
    total = g["売上税抜"].sum()
    g["売上構成比"] = g["売上税抜"] / total if total else 0
    g["客単価"] = np.where(g["購入客数"] > 0, g["売上税抜"] / g["購入客数"], 0)
    return g


# ============ 期間集計 ============
def summarize_period(daily: pd.DataFrame, start: str, end: str) -> dict:
    """期間集計。入店データが無い店舗では来店系KPIが None になる。"""
    d = daily[(daily["date"] >= start) & (daily["date"] <= end)]
    if len(d) == 0:
        return {}

    buyers = int(d["購入客数"].sum())
    qty = int(d["購入点数"].sum())
    sales = int(d["売上税抜"].sum())

    # 来店客数はNaNのことがある（入店データ無し店舗）
    visitors_raw = d["来店客数"].sum(skipna=True)
    has_visitors = d["来店客数"].notna().any() and visitors_raw > 0
    visitors = int(visitors_raw) if has_visitors else None

    return {
        "期間": f"{start}〜{end}",
        "営業日数": int((d["購入客数"] > 0).sum()),
        "来店客数": visitors,
        "購入客数": buyers,
        "売上税抜": sales,
        "売上税込": int(d["売上税込"].sum()),
        "購買率": (buyers / visitors) if visitors else None,
        "客単価": (sales / buyers) if buyers else 0,
        "平均購入数": (qty / buyers) if buyers else 0,
        "商品単価": (sales / qty) if qty else 0,
        "来店単価": (sales / visitors) if visitors else None,
    }


def build_category_summary(cat_detail: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """カテゴリ別の売上・販売数・構成比・平均単価。"""
    d = cat_detail[(cat_detail["date"] >= start) & (cat_detail["date"] <= end)]
    if len(d) == 0:
        return pd.DataFrame()
    g = d.groupby("category").agg(売上=("sales", "sum"), 販売数=("qty", "sum")).reset_index()
    ts, tq = g["売上"].sum(), g["販売数"].sum()
    g["売上構成比"] = g["売上"] / ts if ts else 0
    g["販売数構成比"] = g["販売数"] / tq if tq else 0
    g["平均単価"] = np.where(g["販売数"] > 0, g["売上"] / g["販売数"], 0)
    return g.sort_values("売上", ascending=False).reset_index(drop=True)


def build_priceband_summary(pos_all: pd.DataFrame, store: StoreConfig,
                            start: str, end: str) -> pd.DataFrame:
    """価格帯別。実商品ライン（子＋通常、物販のみ）で集計。"""
    d = pos_all[(pos_all["store"] == store.name) &
                (pos_all["date"] >= start) & (pos_all["date"] <= end)]
    real = d[(d["is_child"] | d["is_normal"]) & (~d["line_category"].isin(NON_MERCH))].copy()
    if len(real) == 0:
        return pd.DataFrame()
    real["band"] = real["line_price"].map(price_band)
    g = real.groupby("band").agg(売上=("line_amount", "sum"), 販売数=("line_qty", "sum")).reset_index()
    order = [b[2] for b in PRICE_BANDS]
    g["band"] = pd.Categorical(g["band"], categories=order, ordered=True)
    g = g.sort_values("band").reset_index(drop=True)
    ts, tq = g["売上"].sum(), g["販売数"].sum()
    g["売上構成比"] = g["売上"] / ts if ts else 0
    g["販売数構成比"] = g["販売数"] / tq if tq else 0
    return g


# ============ 課題診断 ============
def diagnose(daily: pd.DataFrame, store: StoreConfig,
             cur_start: str, cur_end: str, prev_start: str, prev_end: str) -> dict:
    """
    どのKPIが課題なのかを特定する。

    売上 = 来店客数 × 購買率 × 平均購入数 × 商品単価 に分解し、
    各要素の前期比と寄与度を出して「どこが効いた/効かなかった」を数値で示す。

    入店データが無い店舗（下北沢）では、来店客数・購買率を除いた
    「客単価 × 購入客数」の分解にフォールバックする。
    """
    cur = summarize_period(daily, cur_start, cur_end)
    prev = summarize_period(daily, prev_start, prev_end)
    if not cur or not prev:
        return {"store": store.name, "error": "データ不足"}

    cur_days = max(cur["営業日数"], 1)
    prev_days = max(prev["営業日数"], 1)

    factors = {}
    if store.has_entry_data and cur["来店客数"] and prev["来店客数"]:
        factors["来店客数(日平均)"] = (cur["来店客数"] / cur_days, prev["来店客数"] / prev_days)
        factors["購買率"] = (cur["購買率"], prev["購買率"])
    else:
        # 入店データが無い店舗は購入客数で代替
        factors["購入客数(日平均)"] = (cur["購入客数"] / cur_days, prev["購入客数"] / prev_days)
    factors["平均購入数"] = (cur["平均購入数"], prev["平均購入数"])
    factors["商品単価"] = (cur["商品単価"], prev["商品単価"])

    breakdown = []
    for name, (c, p) in factors.items():
        if not p or p <= 0 or not c or c <= 0:
            continue
        breakdown.append({
            "要素": name,
            "今期": c,
            "前期": p,
            "変化率": c / p - 1,
            "寄与": float(np.log(c / p)),
        })
    bd = pd.DataFrame(breakdown)
    if len(bd):
        total = bd["寄与"].abs().sum()
        bd["寄与度"] = bd["寄与"].abs() / total if total else 0
        bd = bd.sort_values("寄与")     # マイナス寄与（＝足を引っ張った要素）が先頭

    cur_spd = cur["売上税抜"] / cur_days
    prev_spd = prev["売上税抜"] / prev_days

    bottleneck = None
    if len(bd) and bd.iloc[0]["寄与"] < 0:
        bottleneck = bd.iloc[0]["要素"]

    return {
        "store": store.name,
        "今期日商": cur_spd,
        "前期日商": prev_spd,
        "日商変化率": (cur_spd / prev_spd - 1) if prev_spd else 0,
        "内訳": bd.to_dict("records") if len(bd) else [],
        "ボトルネック": bottleneck,
        "入店データ": store.has_entry_data,
        "今期": cur,
        "前期": prev,
    }
