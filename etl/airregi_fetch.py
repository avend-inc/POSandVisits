"""
Airレジ（下北沢）から会計明細CSVを自動で取ってくる。

【全体像】cashier_fetch と同じ考え方の自動取得。
  1. Airレジ バックオフィスへアクセス → AirID（リクルートID）でログイン。
  2. 会計明細（取引履歴）の画面へ移動し、期間を指定する。
  3. 「CSVダウンロード」を押して明細CSV（cp932）を落とす。
  → adapters.adapt_airregi が読める「1明細1行」CSVを返す。

【まだサイトのDOMを確定していない段階での使い方（調査モード）】
    python -m etl.airregi_fetch --discover
  ログイン後の画面のナビ・CSV/ダウンロード系要素・<form>・日付入力を
  stdout に洗い出す。Actions のログだけで「本物のURL/ボタンの目印」を確定できる。

【確定した目印は環境変数で上書きできる（コードを触らず調整）】
    AIRREGI_LOGIN_URL     … ログイン開始URL（既定: https://airregi.jp/）
    AIRREGI_SALES_URL     … 会計明細（取引履歴）画面のURL
    AIRREGI_CSV_SELECTOR  … CSVダウンロードボタンのCSSセレクタ
    AIRREGI_DATE_FROM_SEL … 期間開始の入力欄セレクタ
    AIRREGI_DATE_TO_SEL   … 期間終了の入力欄セレクタ
    AIRREGI_DEBUG=1       … 画面構造ダンプを常に出す

【ID/パスワード】
  AIRREGI_ID / AIRREGI_PW（GitHub Secrets か .env）。ソースには書かない。
"""
from __future__ import annotations

import argparse
import re
import sys
import time

from .browser import browser_page, dump_page
from .settings import (BROWSER_TIMEOUT_MS, EtlError, RETRIES, RETRY_WAIT_SEC,
                       load_dotenv, require_env)

# ------------------------------------------------------------
# 取得先（既定値。実DOM確定後に定数 or 環境変数で上書き）
# ------------------------------------------------------------
AIRREGI_LOGIN_URL = "https://airregi.jp/"
# 会計明細（取引履歴）画面。実URLは調査モードで確定して上書きする。
AIRREGI_SALES_URL = "https://airregi.jp/CLP/view/salesList/"

# ログイン欄の候補（AirIDは1画面 or ID→次へ→パスワードの2段の両対応）
LOGIN_ID_SELECTORS = [
    'input[name="loginId"]', 'input[name="username"]', 'input[name="email"]',
    'input[type="email"]', 'input#username', 'input#loginId',
    'input[type="text"]',
]
LOGIN_PW_SELECTORS = [
    'input[name="password"]', 'input[type="password"]', 'input#password',
]
LOGIN_SUBMIT_TEXTS = ["ログイン", "サインイン", "次へ", "ログインする", "Login"]

# CSV出力ボタンの文字候補（cashierと同系統）
CSV_TEXTS = ["CSVダウンロード", "CSV出力", "CSVエクスポート", "CSV",
             "ダウンロード", "エクスポート", "明細ダウンロード"]

# 期間指定の入力欄の候補
DATE_FROM_SELECTORS = [
    'input[name="dateFrom"]', 'input[name="fromDate"]', 'input[name="startDate"]',
    'input[name="from"]', 'input#dateFrom', 'input#fromDate',
]
DATE_TO_SELECTORS = [
    'input[name="dateTo"]', 'input[name="toDate"]', 'input[name="endDate"]',
    'input[name="to"]', 'input#dateTo', 'input#toDate',
]
SEARCH_TEXTS = ["検索", "表示", "絞り込み", "この条件で検索", "適用", "更新"]


def _env(name: str) -> str | None:
    import os
    v = (os.environ.get(name) or "").strip()
    return v or None


def _first_visible(page, selectors: list[str]):
    """候補セレクタの中から、最初に「見えている」要素を返す。無ければ None。"""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            n = loc.count()
            for i in range(min(n, 5)):
                el = loc.nth(i)
                if el.is_visible():
                    return el
        except Exception:
            continue
    return None


def _click_by_text(page, texts: list[str]) -> str | None:
    """文字を含むボタン/リンクを押す。押した文字を返す。無ければ None。"""
    for t in texts:
        if not t:
            continue
        for sel in (f'button:has-text("{t}")', f'a:has-text("{t}")',
                    f'[role=button]:has-text("{t}")', f'input[value="{t}"]'):
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    loc.first.click()
                    return t
            except Exception:
                continue
    return None


# ------------------------------------------------------------
# ログイン
# ------------------------------------------------------------
def login(page, airid: str, password: str) -> None:
    """AirID でログインする。1画面型／ID→次へ→パスワードの2段型の両方に対応。"""
    url = _env("AIRREGI_LOGIN_URL") or AIRREGI_LOGIN_URL
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass

    # ID入力
    id_field = _first_visible(page, LOGIN_ID_SELECTORS)
    if id_field is None:
        dump_page(page, "airregi_login_no_id_field")
        raise EtlError("Airレジのログイン画面でID入力欄が見つかりませんでした。"
                       "debug/airregi_login_no_id_field を確認してください。")
    id_field.fill(airid)

    # パスワード欄が同じ画面にあれば入れる。無ければ「次へ」を押して2段目へ。
    pw_field = _first_visible(page, LOGIN_PW_SELECTORS)
    if pw_field is None:
        _click_by_text(page, ["次へ", "続ける", "ログイン"])
        try:
            page.wait_for_selector(",".join(LOGIN_PW_SELECTORS), timeout=20_000)
        except Exception:
            pass
        pw_field = _first_visible(page, LOGIN_PW_SELECTORS)
    if pw_field is None:
        dump_page(page, "airregi_login_no_pw_field")
        raise EtlError("Airレジのログイン画面でパスワード入力欄が見つかりませんでした。"
                       "debug/airregi_login_no_pw_field を確認してください。")
    pw_field.fill(password)

    # ログイン実行
    if not _click_by_text(page, LOGIN_SUBMIT_TEXTS):
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
    try:
        page.wait_for_load_state("networkidle", timeout=45_000)
    except Exception:
        pass

    # ログイン失敗（まだログイン画面のまま）を早めに検知する
    if _first_visible(page, LOGIN_PW_SELECTORS) is not None and _looks_like_login(page):
        dump_page(page, "airregi_login_failed")
        raise EtlError("Airレジのログインに失敗した可能性があります"
                       "（ID/PW誤り、または追加認証）。debug/airregi_login_failed を確認してください。")


def _looks_like_login(page) -> bool:
    try:
        u = (page.url or "").lower()
        return any(k in u for k in ("login", "signin", "auth", "connect"))
    except Exception:
        return False


# ------------------------------------------------------------
# 調査モード — ログイン後の画面構造を stdout に洗い出す
# ------------------------------------------------------------
def discover(page) -> None:
    """ナビ・CSV/DL系要素・<form>・日付入力を洗い出して表示（目印確定用）。"""
    print(f"  現在URL: {page.url}")
    js = r"""
    () => {
      const cut = (s,n=60) => (s||'').toString().replace(/\s+/g,' ').trim().slice(0,n);
      const KW = ['csv','download','ダウンロード','エクスポート','export','出力'];
      const hasKw = s => { s=(s||'').toLowerCase(); return KW.some(k => s.includes(k.toLowerCase())); };
      const vis = el => { const r = el.getBoundingClientRect(); return !!(r.width||r.height); };
      const nav = [...document.querySelectorAll('nav a, .sidebar a, aside a, .menu a, header a, a')]
        .map(a => ({text:cut(a.innerText,30), href:a.getAttribute('href')}))
        .filter(x => (x.text||x.href)).slice(0,80);
      const dl = [...document.querySelectorAll('a,button,input,[role=button]')]
        .filter(el => hasKw((el.innerText||'')+' '+(el.value||'')+' '+(el.className||'')+' '+
                    [...(el.attributes||[])].map(a=>a.name+'='+a.value).join(' ')))
        .map(el => ({tag:el.tagName, text:cut(el.innerText||el.value,40),
                     vis:vis(el), href:el.getAttribute&&el.getAttribute('href'),
                     onclick:cut(el.getAttribute&&el.getAttribute('onclick'),80)})).slice(0,40);
      const forms = [...document.querySelectorAll('form')]
        .map(f => ({action:f.getAttribute('action'), method:f.getAttribute('method')})).slice(0,15);
      const dates = [...document.querySelectorAll('input')]
        .filter(i => ['date','text'].includes((i.type||'').toLowerCase()))
        .map(i => ({name:i.name, id:i.id, type:i.type, ph:cut(i.placeholder,20)}))
        .filter(x => (x.name||x.id)).slice(0,30);
      return {nav, dl, forms, dates};
    }
    """
    try:
        info = page.evaluate(js)
    except Exception as e:
        print(f"  （構造の取得に失敗: {e}）")
        dump_page(page, "airregi_discover")
        return
    print("  --- ナビ/リンク（text ← href）---")
    for x in info.get("nav", []):
        print(f"    {x.get('text','')!r:40} ← {x.get('href')}")
    print("  --- CSV/ダウンロード系 ---")
    for x in info.get("dl", []):
        print(f"    <{x.get('tag')}> {x.get('text','')!r} vis={x.get('vis')} "
              f"href={x.get('href')} onclick={x.get('onclick')}")
    print("  --- <form> action/method ---")
    for x in info.get("forms", []):
        print(f"    action={x.get('action')} method={x.get('method')}")
    print("  --- 日付らしき入力欄 ---")
    for x in info.get("dates", []):
        print(f"    name={x.get('name')} id={x.get('id')} type={x.get('type')} ph={x.get('ph')}")
    dump_page(page, "airregi_discover")


# ------------------------------------------------------------
# 会計明細画面へ移動＋期間指定
# ------------------------------------------------------------
def open_sales(page) -> None:
    url = _env("AIRREGI_SALES_URL") or AIRREGI_SALES_URL
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        # URL直打ちで行けない場合はナビから「会計」「売上」「明細」を辿る
        if not _click_by_text(page, ["会計履歴", "取引履歴", "会計明細", "売上", "明細"]):
            dump_page(page, "airregi_sales_nav_notfound")
            raise EtlError("Airレジの会計明細画面へ移動できませんでした。"
                           "debug/airregi_sales_nav_notfound を確認してください。")


def set_date_range(page, start_date: str, end_date: str | None = None) -> None:
    end_date = end_date or start_date
    from_sel = _env("AIRREGI_DATE_FROM_SEL")
    to_sel = _env("AIRREGI_DATE_TO_SEL")
    f = page.locator(from_sel).first if from_sel else _first_visible(page, DATE_FROM_SELECTORS)
    t = page.locator(to_sel).first if to_sel else _first_visible(page, DATE_TO_SELECTORS)
    for field, value in ((f, start_date), (t, end_date)):
        if field is None:
            continue
        for v in (value, value.replace("-", "/")):
            try:
                field.fill("")
                field.fill(v)
                break
            except Exception:
                continue
    _click_by_text(page, SEARCH_TEXTS)
    try:
        page.wait_for_load_state("networkidle", timeout=45_000)
    except Exception:
        pass


# ------------------------------------------------------------
# CSVダウンロード
# ------------------------------------------------------------
def download_csv(page, context) -> bytes:
    downloads: list = []
    try:
        page.on("download", lambda d: downloads.append(d))
    except Exception:
        pass

    csv_sel = _env("AIRREGI_CSV_SELECTOR")
    clicked = None
    if csv_sel:
        try:
            page.locator(csv_sel).first.click()
            clicked = csv_sel
        except Exception:
            clicked = None
    if clicked is None:
        clicked = _click_by_text(page, CSV_TEXTS)
    if clicked is None:
        dump_page(page, "airregi_csv_button_notfound")
        raise EtlError("Airレジの「CSVダウンロード」ボタンが見つかりませんでした。"
                       "debug/airregi_csv_button_notfound を確認してください。")

    # ダウンロード確定モーダルがあれば「ダウンロード/OK」を押す
    time.sleep(1.0)
    _click_by_text(page, ["ダウンロード", "OK", "はい", "実行", "確定"])

    deadline = time.time() + 60
    while not downloads and time.time() < deadline:
        page.wait_for_timeout(500)
    if downloads:
        path = downloads[0].path()
        if path:
            with open(path, "rb") as fh:
                return fh.read()

    # ダウンロードイベントが取れない場合、通信ログからCSVらしきURLを直接GET
    log = getattr(page, "_notime_requests", None) or []
    for entry in reversed(log):
        m = re.search(r"(https?://\S+)", entry)
        if not m:
            continue
        u = m.group(1)
        if any(k in u.lower() for k in ("csv", "export", "download")):
            try:
                resp = context.request.get(u)
                if resp.ok:
                    return resp.body()
            except Exception:
                continue
    dump_page(page, "airregi_csv_download_failed")
    raise EtlError("Airレジの明細CSVをダウンロードできませんでした。"
                   "debug/airregi_csv_download_failed の通信記録を確認してください。")


def decode_csv(data: bytes) -> str:
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ------------------------------------------------------------
# まとめ
# ------------------------------------------------------------
def fetch_range(start_date: str, end_date: str, headless: bool = True) -> str:
    """start_date〜end_date の会計明細CSVを取得して文字列で返す。失敗時3回まで再試行。"""
    airid = require_env("AIRREGI_ID", "Airレジ（AirID）のログインID")
    password = require_env("AIRREGI_PW", "Airレジ（AirID）のパスワード")

    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with browser_page(headless=headless) as (page, context):
                login(page, airid, password)
                if _env("AIRREGI_DEBUG"):
                    discover(page)
                open_sales(page)
                set_date_range(page, start_date, end_date)
                return decode_csv(download_csv(page, context))
        except Exception as e:
            last_error = e
            if attempt < RETRIES:
                wait = RETRY_WAIT_SEC * attempt
                print(f"  {attempt}回目失敗（{e}）。{wait}秒待って再試行します...")
                time.sleep(wait)
    raise EtlError(f"AirレジのCSVを{RETRIES}回試しても取得できませんでした。\n{last_error}")


def fetch(business_date: str, headless: bool = True) -> str:
    """対象日1日ぶんの会計明細CSVを取得して文字列で返す（日次用）。"""
    return fetch_range(business_date, business_date, headless=headless)


def main() -> int:
    ap = argparse.ArgumentParser(description="Airレジ 会計明細CSVの自動取得")
    ap.add_argument("--discover", action="store_true",
                    help="ログイン後の画面構造を洗い出すだけ（目印確定用）")
    ap.add_argument("--date", help="1日ぶん取得（YYYY-MM-DD）")
    ap.add_argument("--from", dest="date_from", help="期間の開始（YYYY-MM-DD）")
    ap.add_argument("--to", dest="date_to", help="期間の終了（YYYY-MM-DD）")
    ap.add_argument("--headful", action="store_true", help="画面を出す（手元デバッグ用）")
    args = ap.parse_args()

    load_dotenv()
    headless = not args.headful

    if args.discover:
        airid = require_env("AIRREGI_ID", "Airレジ（AirID）のログインID")
        password = require_env("AIRREGI_PW", "Airレジ（AirID）のパスワード")
        with browser_page(headless=headless) as (page, context):
            login(page, airid, password)
            discover(page)
            # 会計明細画面へも行ってみて、そこの構造も出す
            try:
                open_sales(page)
                print("\n===== 会計明細画面 =====")
                discover(page)
            except Exception as e:
                print(f"  （会計明細画面へは未到達: {e}）")
        return 0

    if args.date:
        csv_text = fetch(args.date, headless=headless)
    elif args.date_from and args.date_to:
        csv_text = fetch_range(args.date_from, args.date_to, headless=headless)
    else:
        print("使い方: --discover か、--date か、--from/--to を指定してください。")
        return 2
    print(csv_text[:2000])
    print(f"...（全 {len(csv_text)} 文字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
