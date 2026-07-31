"""
CSV → Supabaseに入れる行に変換する

【方針】
  cashier の列の読み解き（バンドル判定・店舗名の表記ゆれ など）は
  すでに実データで検証済みの adapters.py にある。
  ここでは **それをそのまま呼ぶ**。同じロジックを二度書かない。
  （adapters.py は変更禁止ファイル。読み取り専用で使う）
"""
from __future__ import annotations

import sys
from io import StringIO

import pandas as pd

from .settings import CASHIER_REQUIRED_COLUMNS, EtlError, ROOT

# adapters.py はリポジトリの一番上にあるので、そこを読み込み先に追加する
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import adapters  # noqa: E402  （上のsys.path設定より後に読む必要がある）


# ============================================================
# cashier 売上明細
# ============================================================
# ⚠️ cashier のCSVは日付の書き方が混ざる（"2026-03-20" と "2026/6/19" の両方）。
#    新しい pandas はこれを1列に混ぜて渡すとエラーで止まってしまう。
#    adapters.py は変更禁止ファイルなので、**渡す前にここで書き方をそろえる**。
DATE_COLUMNS = {
    "伝票：精算日付（締日）": "%Y-%m-%d",
    "伝票：処理日時":         "%Y-%m-%d %H:%M:%S",
}


def _normalize_date_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for column, fmt in DATE_COLUMNS.items():
        if column not in df.columns:
            continue
        parsed = pd.to_datetime(df[column], format="mixed", errors="coerce")
        if column == "伝票：精算日付（締日）":
            broken = int(parsed.isna().sum())
            if broken:
                print(f"  ⚠️ 精算日付を読めない行が {broken}行 ありました（その行は除外されます）")
        df[column] = parsed.dt.strftime(fmt)
    return df


def parse_cashier_csv(csv_text: str, business_date: str | None) -> pd.DataFrame:
    """
    cashier の明細CSV → 共通スキーマ（adapters.COMMON_COLUMNS）。

    business_date を渡すとその営業日の行だけに絞る。
    None を渡すと絞らず、CSVに入っている全期間の行を返す（backfill用）。
    """
    try:
        df = pd.read_csv(StringIO(csv_text), dtype=str)
    except Exception as e:
        raise EtlError(f"cashier のCSVを読めませんでした: {e}")

    missing = [c for c in CASHIER_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise EtlError(
            "cashier のCSVに必要な列がありません。\n"
            f"  足りない列: {missing}\n"
            f"  実際の列  : {list(df.columns)[:20]}\n"
            "  → ダウンロードしたCSVの種類が違うかもしれません"
            "（「明細」入りのCSVを選ぶ必要があります）。"
        )

    if len(df) == 0:
        return pd.DataFrame(columns=adapters.COMMON_COLUMNS + ["line_no"])

    df = _normalize_date_columns(df)
    df = df[df["伝票：精算日付（締日）"].notna()]
    if len(df) == 0:
        return pd.DataFrame(columns=adapters.COMMON_COLUMNS + ["line_no"])

    out = adapters.adapt_cashier(df, "cashier")

    # 対象日だけに絞る（期間指定がうまく効いていなくても事故らないように）。
    # business_date=None のときは絞らない（backfillで全期間を入れる）。
    if business_date is not None:
        out = out[out["date"] == business_date].copy()
    else:
        out = out.copy()

    # 伝票の中での明細の並び順。これが一意キーの一部になる。
    # 期間一括のときは伝票番号(tx_id)ごとに数えれば、日をまたいでも正しく並ぶ。
    out["line_no"] = out.groupby("tx_id").cumcount()
    return out


def cashier_rows(df: pd.DataFrame, store_id_of) -> list[dict]:
    """
    共通スキーマのDataFrame → sales テーブルに入れる行の一覧。

    store_id_of : 店舗名を渡すと store_id を返す関数
    """
    rows: list[dict] = []
    for record in df.to_dict(orient="records"):
        rows.append({
            "business_date": record["date"],
            "store_id":      store_id_of(record["store"]),
            "pos_name":      record["pos_name"],
            "tx_id":         record["tx_id"],
            "line_no":       int(record["line_no"]),
            "ts":            record["ts"],
            "sales_ex_tax":  float(record["sales_ex_tax"]),
            "sales_in_tax":  float(record["sales_in_tax"]),
            "tx_qty":        float(record["tx_qty"]),
            "line_category": record["line_category"],
            "line_price":    float(record["line_price"]),
            "line_qty":      float(record["line_qty"]),
            "line_amount":   float(record["line_amount"]),
            "bundle_code":   record["bundle_code"] or "",
            "is_parent":     bool(record["is_parent"]),
            "is_child":      bool(record["is_child"]),
            "is_normal":     bool(record["is_normal"]),
        })
    return rows


# ============================================================
# デジテール 来店数
# ============================================================
DIGITEL_COLUMN_MAP = {
    "日付":            "business_date",
    "無人入店/解錠数": "visitors",
    "無人常連来店数":  "repeat_visitors",
    "無人購買回数":    "purchases",
    "無人購買率 (%)":  "purchase_rate",
}


def _to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def visits_rows(csv_text: str, store_id: int,
                business_date: str | None) -> list[dict]:
    """
    デジテールのCSV → visits テーブルに入れる行の一覧。

    business_date を渡すとその営業日の行だけを返す。
    None を渡すと、CSVに入っている全期間の各日を返す（backfill用）。
    """
    try:
        df = pd.read_csv(StringIO(csv_text), dtype=str)
    except Exception as e:
        raise EtlError(f"デジテールのCSVを読めませんでした: {e}")

    if len(df) == 0:
        return []

    df = df.rename(columns=DIGITEL_COLUMN_MAP)
    if "business_date" not in df.columns:
        raise EtlError(
            "デジテールのCSVの見出しが想定と違います。\n"
            f"  実際: {list(df.columns)}\n"
            "  → サイト側で列名が変わった可能性があります。"
            "etl/rows.py の DIGITEL_COLUMN_MAP を直してください。"
        )

    df["business_date"] = df["business_date"].astype(str).str.strip()
    if business_date is not None:
        df = df[df["business_date"] == business_date]

    rows = []
    for record in df.to_dict(orient="records"):
        day = business_date or str(record.get("business_date") or "").strip()
        if not day:
            continue
        rows.append({
            "business_date":   day,
            "store_id":        store_id,
            "source":          "digitel",
            "visitors":        _to_int(record.get("visitors")),
            "repeat_visitors": _to_int(record.get("repeat_visitors")),
            "purchases":       _to_int(record.get("purchases")),
            "purchase_rate":   _to_float(record.get("purchase_rate")),
        })
    return rows


# ============================================================
# デジテール 売上（無人店の売上をデジテールから取る店＝伊予松前 など）
# ============================================================
# デジテール売上CSVの列（isMujin=0＝無人+有人）:
#   日付,合計売上金額,無人売上金額,有人売上金額,平均購買単価,売り上げ回数
DIGITEL_SALES_MAP = {
    "日付":         "date",
    "合計売上金額": "total",
    "平均購買単価": "avg",
    "売り上げ回数": "count",
}
# sales の pos_name。既存POS(cashier/SIPOS)と混ざらないよう別名にする。
DIGITEL_SALES_POS = "DIGITEL"


def digitel_sales_rows(csv_text: str, store_id: int,
                       business_date: str | None) -> list[dict]:
    """
    デジテールの“売上”CSV → sales テーブルに入れる行の一覧。

    デジテールは「1日の合計売上」と「売り上げ回数(取引数)」しか出ないため、
    その日の合計を回数ぶんの取引に割り振って入れる（＝取引数・客単価が正しく出る）。
    合計が端数でズレないよう、基準額＋余りを先頭の取引から1円ずつ足して合わせる。
    明細（商品カテゴリ・点数）は無いので line_category='（明細なし）'。
    """
    try:
        df = pd.read_csv(StringIO(csv_text), dtype=str)
    except Exception as e:
        raise EtlError(f"デジテールの売上CSVを読めませんでした: {e}")
    if len(df) == 0:
        return []

    df = df.rename(columns=DIGITEL_SALES_MAP)
    if "date" not in df.columns or "total" not in df.columns or "count" not in df.columns:
        raise EtlError(
            "デジテール売上CSVの見出しが想定と違います。\n"
            f"  実際: {list(df.columns)}\n"
            "  → etl/rows.py の DIGITEL_SALES_MAP を直してください。"
        )

    df["date"] = df["date"].astype(str).str.strip()
    if business_date is not None:
        df = df[df["date"] == business_date]

    rows: list[dict] = []
    for record in df.to_dict(orient="records"):
        day = str(record.get("date") or "").strip()
        total = _to_int(record.get("total")) or 0
        n = _to_int(record.get("count")) or 0
        if not day or n <= 0 or total <= 0:
            continue
        base, rem = divmod(total, n)          # 合計を回数で割る（端数は rem 件に1円ずつ）
        for i in range(n):
            amount = base + (1 if i < rem else 0)
            rows.append({
                "business_date": day,
                "store_id":      store_id,
                "pos_name":      DIGITEL_SALES_POS,
                "tx_id":         f"{day}-{i}",     # その日の擬似取引番号（回数ぶん）
                "line_no":       0,
                "ts":            None,
                "sales_ex_tax":  round(amount / 1.10, 2),
                "sales_in_tax":  float(amount),
                "tx_qty":        None,             # 点数（商品数）はデジテールに無い
                "line_category": "（明細なし）",
                "line_price":    0.0,
                "line_qty":      0.0,
                "line_amount":   float(amount),
                "bundle_code":   "",
                "is_parent":     False,
                "is_child":      False,
                "is_normal":     True,
            })
    return rows


def digitel_detail_rows(sales_csv: str, details_csv: str, store_id: int,
                        business_date: str | None = None) -> list[dict]:
    """
    デジテールの“売上明細（商品別）”ZIP → sales テーブルに入れる行の一覧。

    入力（fetch_sales_detail の戻り）:
      sales_csv    : 取引単位。列 id,type,balance(税込),discount,createdOn,paymentType ほか
      details_csv  : 商品明細。列 transactionId,name,price,taxRate,taxInclusionType,
                     quantity,createdOn,discount,genre(=カテゴリ),subgenre(=価格帯) ほか

    仕組み:
      ・明細1行 = sales 1行（line_no は取引内の連番）。カテゴリ=genre、数量=quantity、
        金額(税抜)= 単価×数量 − 値引（税込商品は税抜換算してから）。
      ・取引の税込合計(sales_in_tax)は取引CSVの balance を正とし、各明細行に同じ値を持たせる
        （export 側で tx_id 単位に1回だけ数えられる。cashierと同じ作り）。
      ・税抜合計(sales_ex_tax)は明細の税抜金額の合計。
    """
    try:
        sdf = pd.read_csv(StringIO(sales_csv), dtype=str) if sales_csv.strip() else pd.DataFrame()
    except Exception:
        sdf = pd.DataFrame()
    try:
        ddf = pd.read_csv(StringIO(details_csv), dtype=str)
    except Exception as e:
        raise EtlError(f"デジテール売上明細CSVを読めませんでした: {e}")
    if len(ddf) == 0:
        return []

    # 取引CSV: id(取引ID) → 税込合計(balance)・支払方法・日時
    tx_in_tax: dict[str, float] = {}
    tx_paytype: dict[str, str] = {}
    if len(sdf):
        for r in sdf.to_dict(orient="records"):
            tid = str(r.get("id") or "").strip()
            if not tid:
                continue
            tx_in_tax[tid] = _to_float(r.get("balance")) or 0.0
            tx_paytype[tid] = str(r.get("paymentType") or "").strip()

    def _date_of(dt: str) -> str:
        return (str(dt or "").strip().replace("/", "-")[:10])

    # 明細を取引IDごとにまとめ、行を組み立てる
    by_tx: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in ddf.to_dict(orient="records"):
        tid = str(r.get("transactionId") or "").strip()
        if not tid:
            continue
        if tid not in by_tx:
            by_tx[tid] = []
            order.append(tid)
        by_tx[tid].append(r)

    rows: list[dict] = []
    for tid in order:
        lines = by_tx[tid]
        # 取引の日付は明細の createdOn から
        day = _date_of(lines[0].get("createdOn"))
        if not day:
            continue
        if business_date is not None and day != business_date:
            continue

        # まず各明細の税抜金額を計算 → 取引の税抜合計
        parsed = []
        tx_ex = 0.0
        tx_qty = 0.0
        for ln in lines:
            qty = _to_float(ln.get("quantity")) or 0.0
            price = _to_float(ln.get("price")) or 0.0
            rate = _to_float(ln.get("taxRate")) or 0.0
            disc = _to_float(ln.get("discount")) or 0.0
            incl = (str(ln.get("taxInclusionType") or "").strip().lower() == "inclusive")
            unit_ex = price / (1.0 + rate) if (incl and rate > 0) else price
            disc_ex = disc / (1.0 + rate) if (incl and rate > 0) else disc
            net_ex = unit_ex * qty - disc_ex
            if net_ex < 0:
                net_ex = 0.0
            cat = (str(ln.get("genre") or "").strip() or "その他")
            parsed.append({"cat": cat, "qty": qty, "unit_ex": unit_ex,
                           "net_ex": net_ex, "ts": str(ln.get("createdOn") or "").strip()})
            tx_ex += net_ex
            tx_qty += qty

        # 取引の税込合計は取引CSVの balance を正に。無ければ税抜合計から税込換算。
        tx_in = tx_in_tax.get(tid)
        if tx_in is None:
            tx_in = round(tx_ex * 1.10, 2)

        for i, p in enumerate(parsed):
            rows.append({
                "business_date": day,
                "store_id":      store_id,
                "pos_name":      DIGITEL_SALES_POS,
                "tx_id":         tid,
                "line_no":       i,
                "ts":            p["ts"] or None,
                "sales_ex_tax":  round(tx_ex, 2),
                "sales_in_tax":  round(float(tx_in), 2),
                "tx_qty":        int(tx_qty) if tx_qty else None,
                "line_category": p["cat"],
                "line_price":    round(p["unit_ex"], 2),
                "line_qty":      float(p["qty"]),
                "line_amount":   round(p["net_ex"], 2),
                "bundle_code":   "",
                "is_parent":     False,
                "is_child":      False,
                "is_normal":     True,
            })
    return rows
