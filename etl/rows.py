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
