"""
LINE配信データの取り込み（デジテールストア）

【使い方】
  python -m etl.line_fetch --probe                  どこにデータがあるか調べる
  python -m etl.line_fetch --all                    過去分をすべて取り込む（初回）
  python -m etl.line_fetch --from 2024-01-01        指定日から今日まで
  python -m etl.line_fetch                          前日ぶんだけ（毎日の自動実行）

【この作りにした理由】
  来店数と同じ仕組みをそのまま使う。デジテールストア（開発元＝ネブラスカ）は
  来店・売上・LINE配信を同じ管理画面で持っているので、
    ブラウザでログイン → その状態のままCSVのURLを叩く → 整えて Supabase へ
  という流れを etl/digitel_fetch.py と共有できる。
  **認証情報も同じ**（DIGITAIL_ID / DIGITAIL_PW）。新しく登録するものは無い。

【店舗の見つけ方】
  来店数と同じ digitel_fetch.discover_stores() で「店舗名 → スラッグ」を拾う。
  LINEのCSVは店舗ごとのURL（/{スラッグ}/... ）にある想定で、
  実際のパスは環境変数で指定できるようにしてある（下記）。

【設定（GitHub の Variables。Secrets は既存の DIGITAIL_* をそのまま使う）】
  DIGITEL_LINE_BROADCAST_PATH  配信実績CSVのパス。既定 "/{slug}/line/broadcasts/download"
  DIGITEL_LINE_FRIENDS_PATH    友だち数CSVのパス。既定 "/{slug}/line/friends/download"
  DIGITEL_LINE_SOURCES_PATH    流入経路CSVのパス。既定 "/{slug}/line/sources/download"
  DIGITEL_LINE_COLMAP          CSVの見出しが想定と違うときの対応表(JSON)
  DIGITEL_LINE_HISTORY_FROM    --all の開始日。既定 "2019-01-01"
  ※ パスには {slug} {from} {to} を差し込める。

【まだ確認できていないこと】
  デジテールのLINE画面の実際のURLとCSVの列名は未確認。
  `--probe`（または Actions の「デジテール店舗一覧の調査」）を1回流すと、
  画面内のリンクと店舗ページ配下のパスを書き出すので、それを見て上を埋める。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

from . import digitel_fetch
from .browser import browser_page, dump_page
from .settings import (
    DIGITEL_BASE_URL,
    JST,
    EtlError,
    load_dotenv,
    new_run_id,
    validate_date,
    yesterday_jst,
)
from .supabase_client import Supabase


# ============================================================
#  設定の読み出し
# ============================================================
def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _require(name: str) -> str:
    v = _env(name)
    if not v:
        raise EtlError(
            f"{name} が未設定です。\n"
            f"  → GitHub の Settings → Secrets and variables → Actions に登録してください。\n"
            f"  → 何を入れるかは etl/line_fetch.py の先頭に書いてあります。"
        )
    return v


# CSVの見出し → こちらの列名。実物に合わせて DIGITEL_LINE_COLMAP で上書きできる。
# 1つの列に複数の呼び名を並べてあるのは、画面の言い回しが分からないうちに
# 決め打ちすると外れるため。
DEFAULT_ALIASES: dict[str, list[str]] = {
    "broadcast_id": ["配信ID", "メッセージID", "ID", "broadcast_id"],
    "sent_at":      ["配信日時", "送信日時", "配信日", "日時", "sent_at"],
    "title":        ["配信名", "メッセージ名", "タイトル", "件名", "title"],
    "kind":         ["配信種別", "種別", "配信タイプ", "type"],
    "target":       ["配信対象", "ターゲット", "セグメント", "target"],
    "delivered":    ["配信通数", "送信数", "配信数", "delivered", "sent"],
    "opened":       ["開封数", "インプレッション", "表示回数", "opened", "impressions"],
    "clicked":      ["クリック数", "クリック", "clicked", "clicks"],
    "click_users":  ["クリックユーザー数", "クリック人数", "click_users"],
    "blocked":      ["ブロック数", "ブロック", "blocked"],
    "coupon_used":  ["クーポン使用数", "クーポン利用数", "coupon_used"],
    # 友だち数（日次）
    "date":         ["日付", "date", "集計日"],
    "friends":      ["友だち数", "有効友だち数", "friends"],
    "followers":    ["累計友だち数", "追加友だち数(累計)", "followers"],
    "added":        ["新規友だち数", "友だち追加数", "追加", "added"],
    "net":          ["純増", "増減", "net"],
    "targeted":     ["ターゲットリーチ", "配信可能数", "targeted"],
    # 流入経路
    "source":       ["経路", "流入経路", "追加経路", "source"],
}

# CSVのパス。実物が分かったら Variables で上書きする（コードは触らない）。
DEFAULT_PATHS = {
    "BROADCAST": "/{slug}/line/broadcasts/download",
    "FRIENDS":   "/{slug}/line/friends/download",
    "SOURCES":   "/{slug}/line/sources/download",
}


def _aliases() -> dict[str, list[str]]:
    """既定の対応表に、DIGITEL_LINE_COLMAP で指定された呼び名を足す。"""
    out = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    raw = _env("DIGITEL_LINE_COLMAP")
    if not raw:
        return out
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError as e:
        raise EtlError(f"DIGITEL_LINE_COLMAP がJSONとして読めません: {e}") from e
    for key, names in extra.items():
        vals = names if isinstance(names, list) else [names]
        out.setdefault(key, [])
        out[key] = [str(v) for v in vals] + [v for v in out[key] if v not in vals]
    return out


def _url_for(kind: str, slug: str, start: str, end: str) -> str:
    """CSVのURLを組み立てる。パスは Variables で差し替えられる。"""
    tmpl = _env(f"DIGITEL_LINE_{kind}_PATH") or DEFAULT_PATHS[kind]
    path = (tmpl.replace("{slug}", slug)
                .replace("{from}", start).replace("{to}", end)
                .replace("{start}", start).replace("{end}", end))
    if not path.startswith("http"):
        path = DIGITEL_BASE_URL + ("" if path.startswith("/") else "/") + path
    # 期間の指定がパスに無ければクエリで付ける（来店CSVと同じ形）
    if "{" not in tmpl and "start" not in path and "from" not in path:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}start={start}&end={end}"
    return path


# ============================================================
#  調査モード
# ============================================================
def probe(headless: bool = True) -> int:
    """
    デジテールにログインして、LINE配信データの置き場所を探す。
    来店・売上と同じログイン（DIGITAIL_ID / DIGITAIL_PW）を使う。
    """
    with browser_page(headless=headless) as (page, context):
        digitel_fetch.login(page)
        print(f"  ログイン後のURL: {page.url}")

        stores = digitel_fetch.discover_stores(page)
        print(f"  見つかった店舗: {len(stores)}件")
        for nm, sl in list(sorted(stores.items()))[:10]:
            print(f"     {nm} → {sl}")
        if not stores:
            dump_page(page, "digitel_line_probe_nostores")
            raise EtlError("店舗を1件も見つけられませんでした。debug/ を確認してください。")

        slug = sorted(stores.values())[0]
        print(f"\n  この店舗で調べます: {slug}")

        # 画面内のリンクからLINEらしきものを拾う
        for path in ("", "/line", "/kpi", "/kpi/line", "/message", "/broadcast"):
            url = f"{DIGITEL_BASE_URL}/{slug}{path}"
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                code = resp.status if resp else "?"
            except Exception as e:
                print(f"    {url} → 開けません（{str(e)[:60]}）")
                continue
            page.wait_for_timeout(1200)
            try:
                hrefs = page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.getAttribute('href')).filter(Boolean)")
            except Exception:
                hrefs = []
            line_links = [h for h in hrefs if any(
                k in h.lower() for k in ("line", "message", "broadcast", "friend"))]
            mark = "★" if line_links else " "
            print(f"  {mark} {url} → HTTP {code} / リンク{len(hrefs)}件")
            for h in line_links[:10]:
                print(f"       → {h}")

        dump_page(page, "digitel_line_probe")
        print("\n  ★ が付いたURLと、その下のリンクを見て")
        print("     DIGITEL_LINE_BROADCAST_PATH などを Variables に設定してください。")
    return 0


# ============================================================
#  取り込み本体
# ============================================================
def run(start: str, end: str, headless: bool = True) -> int:
    import pandas as pd

    from .line_rows import broadcast_rows, daily_rows, source_rows

    load_dotenv()
    sb = Supabase()
    run_id = new_run_id()
    aliases = _aliases()

    with browser_page(headless=headless) as (page, context):
        login(page)

        # アカウント一覧。1アカウントだけの運用なら NEBRASKA_ACCOUNTS_URL は空でよい。
        accounts: list[dict] = []
        if _env("NEBRASKA_ACCOUNTS_URL"):
            text = _fetch_csv(context, _url_for("ACCOUNTS", start, end))
            df = pd.read_csv(__import__("io").StringIO(text))
            for _, r in df.iterrows():
                accounts.append({k: r.get(k) for k in df.columns})
        if not accounts:
            accounts = [{"account_id": _env("NEBRASKA_ACCOUNT_ID", "default"),
                         "name": _env("NEBRASKA_ACCOUNT_NAME", "LINE公式アカウント")}]

        # 台帳を先に入れる（配信・日次が外部キーで参照するため）
        acct_rows = [{"account_id": str(a.get("account_id") or "default"),
                      "name": a.get("name"),
                      "basic_id": a.get("basic_id")} for a in accounts]
        sb.upsert("line_accounts", acct_rows, on_conflict="account_id")
        print(f"  アカウント: {len(acct_rows)}件")

        total = {"broadcasts": 0, "daily": 0, "sources": 0}
        for a in acct_rows:
            aid = a["account_id"]

            for kind, table, builder, key in (
                ("BROADCAST", "line_broadcasts", broadcast_rows, "broadcast_id"),
                ("FRIENDS",   "line_daily",      daily_rows,     "date,account_id"),
                ("SOURCES",   "line_sources",    source_rows,    "date,account_id,source"),
            ):
                if not _env(f"NEBRASKA_{kind}_URL"):
                    print(f"  ℹ️ NEBRASKA_{kind}_URL 未設定のためスキップ")
                    continue
                text = _fetch_csv(context, _url_for(kind, start, end, aid))
                rows = builder(text, aid, aliases)
                if rows:
                    sb.upsert(table, rows, on_conflict=key)
                total[table.replace("line_", "")] = total.get(table.replace("line_", ""), 0) + len(rows)
                print(f"  {a.get('name') or aid} / {table}: {len(rows)}行")

    print(f"\n✅ 取り込み完了 {start}〜{end} "
          f"（配信 {total.get('broadcasts', 0)} / 日次 {total.get('daily', 0)} / 経路 {total.get('sources', 0)}）"
          f"  run_id={run_id}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="LINE配信データ（ネブラスカ）を取り込む")
    ap.add_argument("--from", dest="start", default="", help="開始日 YYYY-MM-DD")
    ap.add_argument("--to", dest="end", default="", help="終了日 YYYY-MM-DD（空なら今日）")
    ap.add_argument("--all", action="store_true", help="取れる限り過去まで遡る")
    ap.add_argument("--probe", action="store_true", help="画面構成を調べて debug/ に書き出す")
    ap.add_argument("--headed", action="store_true", help="ブラウザの画面を出す（調査用）")
    args = ap.parse_args()

    load_dotenv()
    if args.probe:
        return probe(headless=not args.headed)

    end = validate_date(args.end) if args.end else datetime.now(JST).date().isoformat()
    if args.all:
        # 「過去分すべて」。LINE公式アカウントの提供開始より前は無いので、そこで止める。
        start = _env("NEBRASKA_HISTORY_FROM", "2019-01-01")
    elif args.start:
        start = validate_date(args.start)
    else:
        start = yesterday_jst()
    if start > end:
        raise EtlError(f"開始日({start})が終了日({end})より後になっています。")
    print(f"LINE配信データを取り込みます: {start} 〜 {end}")
    return run(start, end, headless=not args.headed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EtlError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
