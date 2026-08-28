"""
ネブラスカのCSVを、sql/034_line.sql の表の形に整える。

【なぜ切り分けてあるか】
  取りに行く側（line_fetch.py）と、整える側（ここ）を分けておくと、
  CSVの実物が手に入ったときに、ブラウザを動かさずに整形だけ試せる。
  POSのアダプタ層（adapters.py）と同じ考え方。

【列名の対応】
  CSVの見出しは配信ツールによって違う（「配信通数」「送信数」「delivered」…）。
  line_fetch.DEFAULT_ALIASES に呼び名を並べておき、CSVの見出しと突き合わせる。
  実物が想定と違えば NEBRASKA_COLMAP（JSON）で足せる。コードは触らない。
"""
from __future__ import annotations

import io
import re
from datetime import datetime

import pandas as pd

from .settings import JST, EtlError


def _read(text: str) -> pd.DataFrame:
    """CSVを読む。先頭に説明行が入っていることがあるので、見出しらしい行を探す。"""
    if not text or not text.strip():
        return pd.DataFrame()
    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception as e:
        raise EtlError(f"CSVを読めませんでした: {e}") from e
    # 1列しか無い＝区切りがカンマでない、あるいは説明行だけ、の可能性
    if df.shape[1] <= 1:
        for sep in ("\t", ";"):
            try:
                alt = pd.read_csv(io.StringIO(text), sep=sep)
                if alt.shape[1] > df.shape[1]:
                    df = alt
            except Exception:
                pass
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _col(df: pd.DataFrame, key: str, aliases: dict[str, list[str]]) -> str | None:
    """こちらの列名(key)に対応するCSVの見出しを探す。表記ゆれは緩く見る。"""
    if df.empty:
        return None
    norm = {re.sub(r"[\s　()（）]", "", str(c)).lower(): c for c in df.columns}
    for name in aliases.get(key, []):
        k = re.sub(r"[\s　()（）]", "", str(name)).lower()
        if k in norm:
            return norm[k]
    return None


def _int(v) -> int | None:
    """"1,234" や "1,234件" でも数値にする。空・不明は None。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if not s or s in ("-", "—", "‐"):
        return None
    s = re.sub(r"[^\d\-]", "", s)
    if not s or s == "-":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _dt(v) -> datetime | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("年", "-").replace("月", "-").replace("日", " ").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=JST)
        except ValueError:
            continue
    return None


def broadcast_rows(text: str, account_id: str, aliases: dict[str, list[str]]) -> list[dict]:
    """配信ごとの成果 → line_broadcasts"""
    df = _read(text)
    if df.empty:
        return []
    c = {k: _col(df, k, aliases) for k in
         ("broadcast_id", "sent_at", "title", "kind", "target",
          "delivered", "opened", "clicked", "click_users", "blocked", "coupon_used")}
    if not c["sent_at"]:
        raise EtlError(
            "配信CSVに日時の列が見つかりませんでした。\n"
            f"  見出し: {list(df.columns)[:15]}\n"
            "  → NEBRASKA_COLMAP で sent_at の呼び名を指定してください。"
        )
    out = []
    for i, r in df.iterrows():
        ts = _dt(r.get(c["sent_at"]))
        # 配信IDが無いCSVでも一意になるよう、日時＋行番号で作る
        bid = str(r.get(c["broadcast_id"])).strip() if c["broadcast_id"] else ""
        if not bid or bid.lower() == "nan":
            bid = f"{account_id}:{ts.isoformat() if ts else 'na'}:{i}"
        out.append({
            "broadcast_id": bid,
            "account_id": account_id,
            "sent_at": ts.isoformat() if ts else None,
            "business_date": ts.date().isoformat() if ts else None,
            "title": (str(r.get(c["title"])).strip() if c["title"] else None) or None,
            "kind": (str(r.get(c["kind"])).strip() if c["kind"] else None) or None,
            "target": (str(r.get(c["target"])).strip() if c["target"] else None) or None,
            "delivered": _int(r.get(c["delivered"])) if c["delivered"] else None,
            "opened": _int(r.get(c["opened"])) if c["opened"] else None,
            "clicked": _int(r.get(c["clicked"])) if c["clicked"] else None,
            "click_users": _int(r.get(c["click_users"])) if c["click_users"] else None,
            "blocked": _int(r.get(c["blocked"])) if c["blocked"] else None,
            "coupon_used": _int(r.get(c["coupon_used"])) if c["coupon_used"] else None,
        })
    return out


def daily_rows(text: str, account_id: str, aliases: dict[str, list[str]]) -> list[dict]:
    """友だち数の日次 → line_daily"""
    df = _read(text)
    if df.empty:
        return []
    c = {k: _col(df, k, aliases) for k in
         ("date", "friends", "followers", "added", "blocked", "net", "targeted")}
    if not c["date"]:
        raise EtlError(
            "友だちCSVに日付の列が見つかりませんでした。\n"
            f"  見出し: {list(df.columns)[:15]}\n"
            "  → NEBRASKA_COLMAP で date の呼び名を指定してください。"
        )
    out = []
    for _, r in df.iterrows():
        d = _dt(r.get(c["date"]))
        if not d:
            continue
        added = _int(r.get(c["added"])) if c["added"] else None
        blocked = _int(r.get(c["blocked"])) if c["blocked"] else None
        net = _int(r.get(c["net"])) if c["net"] else None
        if net is None and added is not None and blocked is not None:
            net = added - blocked
        out.append({
            "date": d.date().isoformat(),
            "account_id": account_id,
            "friends": _int(r.get(c["friends"])) if c["friends"] else None,
            "followers": _int(r.get(c["followers"])) if c["followers"] else None,
            "added": added,
            "blocked": blocked,
            "net": net,
            "targeted": _int(r.get(c["targeted"])) if c["targeted"] else None,
        })
    return out


def source_rows(text: str, account_id: str, aliases: dict[str, list[str]]) -> list[dict]:
    """友だち追加の経路別 → line_sources

    経路が「行」で来る形（日付・経路・数）と、「列」で来る形（日付・検索・QR…）の
    どちらでも読めるようにする。配信ツールによってどちらもあるため。
    """
    df = _read(text)
    if df.empty:
        return []
    dcol = _col(df, "date", aliases)
    scol = _col(df, "source", aliases)
    acol = _col(df, "added", aliases)
    if not dcol:
        return []

    out = []
    if scol:                      # 行持ち（日付・経路・数）
        for _, r in df.iterrows():
            d = _dt(r.get(dcol))
            src = str(r.get(scol)).strip()
            if not d or not src or src.lower() == "nan":
                continue
            out.append({"date": d.date().isoformat(), "account_id": account_id,
                        "source": src, "added": _int(r.get(acol)) if acol else None})
    else:                         # 列持ち（日付以外の列名がそのまま経路）
        for _, r in df.iterrows():
            d = _dt(r.get(dcol))
            if not d:
                continue
            for col in df.columns:
                if col == dcol:
                    continue
                n = _int(r.get(col))
                if n is None:
                    continue
                out.append({"date": d.date().isoformat(), "account_id": account_id,
                            "source": str(col).strip(), "added": n})
    return out
