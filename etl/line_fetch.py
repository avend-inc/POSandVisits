"""
LINE配信データの取り込み（配信元＝ネブラスカ）

【使い方】
  python -m etl.line_fetch --probe                  どんな画面・CSVがあるか調べる（最初はこれ）
  python -m etl.line_fetch --from 2024-01-01        指定日から今日までを取り込む
  python -m etl.line_fetch                          前日ぶんだけ取り込む（毎日の自動実行）
  python -m etl.line_fetch --all                    取れる限り過去まで遡って取り込む

【この作りにした理由】
  来店数（デジテール）と同じ仕組みをそのまま使っている。
    ブラウザでログイン → その状態のままCSVのURLを叩く → 表に整えて Supabase へ
  デジテールと違うのは、画面の場所（URL）と、CSVの列名を
  **コードに書かず環境変数で指定できる**ようにしたところ。
  ネブラスカの画面構成をこちらで確認できていないので、
  実物に合わせて Secrets / Variables を直せば、コードを触らずに合わせられる。

【設定（GitHub の Secrets / Variables）】
  必須:
    NEBRASKA_LOGIN_URL   ログイン画面のURL
    NEBRASKA_ID          ログインID
    NEBRASKA_PW          パスワード
  任意（実物を見てから埋める。空なら既定値で試す）:
    NEBRASKA_BASE_URL          サイトの入口URL（省略時はログインURLのドメイン）
    NEBRASKA_ID_SELECTOR       ID入力欄の目印
    NEBRASKA_PW_SELECTOR       パスワード入力欄の目印
    NEBRASKA_SUBMIT_SELECTOR   ログインボタンの目印
    NEBRASKA_BROADCAST_URL     配信実績CSVのURL（{from} {to} {account} を差し込める）
    NEBRASKA_FRIENDS_URL       友だち数CSVのURL（同上）
    NEBRASKA_SOURCES_URL       流入経路CSVのURL（同上）
    NEBRASKA_ACCOUNTS_URL      アカウント一覧のURL
    NEBRASKA_COLMAP            列名の対応表（JSON）。CSVの見出しが想定と違うときに使う
                               例: {"delivered":"送信数","opened":"開封"}

【まだ確認できていないこと】
  ネブラスカの実際のURL・画面構成・CSVの列名は未確認。
  --probe を1回流すと、ログイン後の画面にあるリンク・ボタン・入力欄を
  debug/ に書き出すので、それを見て上の変数を埋める。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

from .browser import browser_page, dump_controls, dump_page
from .settings import (
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


def _base_url() -> str:
    base = _env("NEBRASKA_BASE_URL")
    if base:
        return base.rstrip("/")
    login = _require("NEBRASKA_LOGIN_URL")
    # ログインURLからドメインだけ取り出す
    parts = login.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 else login


# CSVの見出し → こちらの列名。実物に合わせて NEBRASKA_COLMAP で上書きできる。
# 1つの列に複数の呼び名を並べてあるのは、配信ツールによって言い回しが違うため。
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


def _aliases() -> dict[str, list[str]]:
    """既定の対応表に、NEBRASKA_COLMAP で指定された呼び名を足す。"""
    out = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    raw = _env("NEBRASKA_COLMAP")
    if not raw:
        return out
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError as e:
        raise EtlError(f"NEBRASKA_COLMAP がJSONとして読めません: {e}") from e
    for key, names in extra.items():
        vals = names if isinstance(names, list) else [names]
        out.setdefault(key, [])
        # 指定されたものを先頭に置く（既定より優先する）
        out[key] = [str(v) for v in vals] + [v for v in out[key] if v not in vals]
    return out


# ============================================================
#  ログイン
# ============================================================
def login(page) -> None:
    """ネブラスカにログインする。目印は環境変数で差し替えられる。"""
    url = _require("NEBRASKA_LOGIN_URL")
    user = _require("NEBRASKA_ID")
    pw = _require("NEBRASKA_PW")

    page.goto(url, wait_until="domcontentloaded")

    # 目印の指定が無ければ、よくある形をこの順で試す
    id_sel = _env("NEBRASKA_ID_SELECTOR")
    pw_sel = _env("NEBRASKA_PW_SELECTOR") or 'input[type="password"]'
    go_sel = _env("NEBRASKA_SUBMIT_SELECTOR") or 'button[type="submit"]'
    id_candidates = [id_sel] if id_sel else [
        'input[type="email"]', 'input[name="email"]', 'input[name="login_id"]',
        'input[name="username"]', 'input[name="id"]', 'input[type="text"]',
    ]

    filled = False
    for sel in id_candidates:
        try:
            page.fill(sel, user, timeout=4000)
            filled = True
            break
        except Exception:
            continue
    if not filled:
        dump_controls(page, "nebraska_login_notfound")
        raise EtlError(
            "ネブラスカのログイン画面で、ID入力欄が見つかりませんでした。\n"
            "  → debug/ に画面の入力欄・ボタンの一覧を書き出しました。\n"
            "  → それを見て NEBRASKA_ID_SELECTOR を指定してください。"
        )

    page.fill(pw_sel, pw)
    page.click(go_sel)
    page.wait_for_load_state("domcontentloaded")

    # ログインできたか。まだログイン画面にいるなら失敗とみなす
    if "login" in (page.url or "").lower() and "dashboard" not in (page.url or "").lower():
        dump_page(page, "nebraska_login_failed")
        raise EtlError(
            "ネブラスカにログインできませんでした（ID/PW誤り・2段階認証・画面変更の可能性）。\n"
            f"  → まずご自分のブラウザで {url} に入れるか確かめてください。\n"
            "  → debug/ に画面を保存しました。"
        )


# ============================================================
#  調査モード
# ============================================================
def probe(headless: bool = True) -> int:
    """
    ログインしたあと、画面にあるリンク・ボタン・入力欄を debug/ に書き出す。
    ネブラスカの画面構成が分からないので、まずこれを1回流して中身を見る。
    """
    with browser_page(headless=headless) as (page, context):
        login(page)
        print(f"  ログイン後のURL: {page.url}")
        dump_controls(page, "nebraska_top")
        dump_page(page, "nebraska_top")

        # 画面内のリンクを一覧にする。CSVの出口や「分析」ページを見つける手がかりにする。
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({t: (e.innerText||'').trim().slice(0,40), h: e.getAttribute('href')}))",
        )
        seen, rows = set(), []
        for l in links:
            h = l.get("h") or ""
            if not h or h.startswith("#") or h in seen:
                continue
            seen.add(h)
            rows.append(f"  {l.get('t') or '(文字なし)':<42} {h}")
        print(f"\n  ---- 画面内のリンク {len(rows)}件 ----")
        for r in rows[:120]:
            print(r)

        hints = [r for r in rows if any(
            k in r.lower() for k in
            ("csv", "download", "export", "分析", "配信", "友だち", "友達", "レポート", "統計")
        )]
        if hints:
            print("\n  ---- CSV・分析まわりに見えるもの ----")
            for r in hints:
                print(r)
        print("\n  この一覧をもとに NEBRASKA_BROADCAST_URL などを決めてください。")
    return 0


# ============================================================
#  CSVの取得
# ============================================================
def _fetch_csv(context, url: str) -> str:
    """ログイン済みの状態でCSVのURLを取りに行く（デジテールと同じやり方）。"""
    resp = context.request.get(url, timeout=120_000)
    if resp.status != 200:
        raise EtlError(f"CSVの取得に失敗しました（HTTP {resp.status}） URL: {url}")
    body = resp.body()
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _url_for(kind: str, start: str, end: str, account: str = "") -> str:
    """URLの雛形に期間とアカウントを差し込む。"""
    tmpl = _env(f"NEBRASKA_{kind}_URL")
    if not tmpl:
        raise EtlError(
            f"NEBRASKA_{kind}_URL が未設定です。\n"
            "  → 先に `python -m etl.line_fetch --probe` を流して、\n"
            "     ネブラスカのどのURLでCSVが取れるかを確かめてください。"
        )
    return (tmpl.replace("{from}", start).replace("{to}", end)
                .replace("{account}", account).replace("{start}", start).replace("{end}", end))


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
