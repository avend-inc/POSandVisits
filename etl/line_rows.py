"""
デジテールから取ったものを、sql/034_line.sql の表の形に整える。

【なぜ切り分けてあるか】
  取りに行く側（line_fetch.py）と、整える側（ここ）を分けておくと、
  ブラウザを動かさずに整形だけを試せる。
  POSのアダプタ層（adapters.py）と同じ考え方。

【2つの入口】
  daily_rows                … 友だち数のCSV（/kpi/members/friends/download）
  broadcast_rows_from_table … 配信履歴の画面の表（CSVの出口が無いため）

【列名の対応】
  見出しが変わっても壊れないよう、line_fetch.DEFAULT_ALIASES に呼び名を
  並べて突き合わせる。想定と違えば DIGITEL_LINE_COLMAP（JSON）で足せる。
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
            "  → DIGITEL_LINE_COLMAP で date の呼び名を指定してください。"
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
    return _drop_before_open(out)


def _drop_before_open(rows: list[dict]) -> list[dict]:
    """アカウントが動き出す前の0埋めを捨てる。

    デジテールの友だちCSVは from を指定しなくても 2019-01-01 から1日1行を返し、
    開設前の日も 0 で埋めてくる。そのまま入れると友だち数のグラフが何年も 0 の
    まま伸び、行数も膨らむ（2026-08-28 の初回取り込みで 61,512行 → 実データは
    5,967行だった）。最初に数字が入る日より前を落とす。
    数字が1日も無いアカウント（未開設）は空で返す。
    """
    rows.sort(key=lambda r: r["date"])
    for i, r in enumerate(rows):
        if (r.get("followers") or 0) > 0 or (r.get("friends") or 0) > 0:
            return rows[i:]
    return []


# ============================================================
#  配信履歴（画面の表）→ line_broadcasts
# ============================================================
#   /{slug}/messages/history の表をそのまま受け取る。CSVの出口が無いため。
#   列は 配信日時 / 配信人数 / メッセージタイプ / 開封率 / メッセージ。
#   ・開封「率」しか無く件数が無いので、opened は率から割り戻して入れる
#     （open_rate に率そのものも残すので、後から見分けられる）
#   ・クリック数・クーポン使用数は画面に無いので null のまま
def broadcast_rows_from_table(table: list[list[str]], account_id: str,
                              start: str = "", end: str = "") -> list[dict]:
    out = []
    for cells in table:
        if len(cells) < 2:
            continue
        ts = _dt(cells[0])
        if not ts:
            continue
        d = ts.date().isoformat()
        if start and d < start:
            continue
        if end and d > end:
            continue
        delivered = _int(cells[1]) if len(cells) > 1 else None
        kind = cells[2].strip() if len(cells) > 2 else None
        rate = None
        if len(cells) > 3:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", cells[3] or "")
            if m:
                rate = float(m.group(1))
        title = (cells[4].strip()[:200] if len(cells) > 4 else None) or None
        out.append({
            # 同じ店・同じ日時の配信は1件。取り直しても上書きになる
            "broadcast_id": f"{account_id}:{ts.isoformat()}",
            "account_id": account_id,
            "sent_at": ts.isoformat(),
            "business_date": d,
            "title": title,
            "kind": kind or None,
            "delivered": delivered,
            "opened": (round(delivered * rate / 100) if (delivered and rate is not None) else None),
            "open_rate": rate,
        })
    return out
