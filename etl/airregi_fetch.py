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
# バックオフィスのログイン画面（宣伝トップ https://airregi.jp/ ではない）。
AIRREGI_LOGIN_URL = "https://airregi.jp/CLP/view/login/"
# バックオフィスのトップ（ログイン後の起点）。
AIRREGI_HOME_URL = "https://airregi.jp/CLP/view/top/"
# 会計データ（ジャーナル＝会計明細）画面。1明細1行のCSVはここから出す。
#   ※ salesList は「売上集計」で列が違う（会計日なし）。会計明細は salesJournal。
AIRREGI_SALES_URL = "https://airregi.jp/CLP/view/salesJournal/"

# ログイン後の「利用する店舗を選択」画面で選ぶ店舗名（部分一致）。
# このAirIDは複数店（SELFURUGI/吉祥寺/本郷/下北沢）を持つため、下北沢を選ぶ。
AIRREGI_STORE_SELECT = "下北沢"

# 立ち上げ調査フラグ。手順は確定したので通常は False（必要時 AIRREGI_DEBUG=1）。
_BRINGUP = False

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


def select_store(page, store_name: str | None = None) -> None:
    """ログイン後の「利用する店舗を選択」画面で目的の店舗を選ぶ。

    このAirIDは複数店を持ち、ログイン直後に choose-store 画面が出る。
    store_name（部分一致）のリンクを押して、その店のバックオフィスへ入る。
    店舗選択画面でなければ何もしない（1店だけのアカウント等）。
    """
    store_name = store_name or _env("AIRREGI_STORE_SELECT") or AIRREGI_STORE_SELECT
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    # choose-store 画面かどうか。URL か「店舗を選択」の見出しで判定。
    on_choose = "choose-store" in url
    if not on_choose:
        try:
            on_choose = page.locator('text=利用する店舗を選択').count() > 0
        except Exception:
            on_choose = False
    if not on_choose:
        return

    # 店舗名（部分一致）のリンク/要素を押す。全角スペース等の表記ゆれは has-text の
    # 部分一致で吸収（"下北沢" は "NOTIME　下北沢店" に含まれる）。
    clicked = False
    for sel in (f'a:has-text("{store_name}")',
                f'.storeList__list__innerBox__name:has-text("{store_name}")',
                f'[class*="storeList"]:has-text("{store_name}")',
                f'li:has-text("{store_name}")',
                f'text={store_name}'):
        try:
            loc = page.locator(sel)
            if loc.count():
                loc.first.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        dump_page(page, "airregi_choose_store_notfound")
        raise EtlError(f"Airレジの店舗選択で「{store_name}」が見つかりませんでした。"
                       "debug/airregi_choose_store_notfound を確認してください。")
    try:
        page.wait_for_load_state("networkidle", timeout=45_000)
    except Exception:
        pass


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
        .filter(x => (x.name||x.id)).slice(0,40);
      // 全ボタン/クリック要素（ラベルが CSV 等でなくても拾う。SPAの出力ボタン特定用）
      const btns = [...document.querySelectorAll('button,[role=button],a,[data-testid]')]
        .map(el => ({tag:el.tagName, text:cut(el.innerText||el.value,40),
                     tid:el.getAttribute&&el.getAttribute('data-testid'),
                     vis:vis(el)}))
        .filter(x => (x.text||x.tid)).slice(0,120);
      return {nav, dl, forms, dates, btns};
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
    print("  --- 全ボタン/クリック要素（text | data-testid | vis）---")
    for x in info.get("btns", []):
        t = x.get("text") or ""
        if not t and not x.get("tid"):
            continue
        print(f"    <{x.get('tag')}> {t!r} tid={x.get('tid')} vis={x.get('vis')}")
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


# ------------------------------------------------------------
# CSVダウンロード（取引履歴→ダウンロードアイコン→ジャーナル履歴(CSV)→
#   モーダルで期間指定→「ダウンロードの準備を開始する」→非同期生成→
#   「ダウンロードする」でCSV取得、という手順）
# ------------------------------------------------------------
JOURNAL_MENU_TEXTS = ["ジャーナル履歴(CSV)", "ジャーナル履歴（CSV）", "ジャーナル履歴"]
PREP_TEXTS = ["ダウンロードの準備を開始する", "ダウンロードの準備を開始",
              "準備を開始する", "準備を開始"]
DL_DONE_TEXTS = ["ダウンロードする"]


def _journal_menu_visible(page) -> bool:
    try:
        return page.get_by_text("ジャーナル履歴").first.is_visible()
    except Exception:
        return False


def _open_download_menu(page) -> bool:
    """取引履歴の右上「ダウンロードアイコン」を押して、CSV種別メニューを開く。

    アイコンなので文字では当てられない。安全・短時間で当てるため:
      ① まず狙いの効くセレクタ（aria-label/title/クラスに download/ダウンロード）
      ② ダメなら「上部ツールバーのアイコン要素」だけを少数・短timeoutで試す
    誤クリックで別メニューが出た場合は Esc で閉じてから次を試す（画面破壊防止）。
    """
    if _journal_menu_visible(page):
        return True

    # ① 狙いの効くセレクタ（速い・安全）
    strong = [_env("AIRREGI_DL_ICON_SEL"),
              'button[aria-label*="ダウンロード"]', 'a[aria-label*="ダウンロード"]',
              '[title*="ダウンロード"]', '[aria-label*="download" i]',
              '[title*="download" i]',
              'button[class*="download" i]', 'a[class*="download" i]',
              '[class*="Download"]', '[class*="csvDownload" i]', '[class*="dlIcon" i]']
    for sel in strong:
        if not sel:
            continue
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    el.click(timeout=1500)
                except Exception:
                    continue
                page.wait_for_timeout(500)
                if _journal_menu_visible(page):
                    return True
        except Exception:
            continue

    # ② 上部ツールバーのアイコン（img/svgを含むクリック要素）を少数だけ試す。
    #    画面上部(y<300)に限定し、誤クリックは Esc で復帰。総数も制限して暴走防止。
    try:
        icons = page.locator(
            'button:has(img), a:has(img), button:has(svg), a:has(svg), '
            '[role=button]:has(svg), img[src*="download" i], img[alt*="ダウンロード"]')
        n = min(icons.count(), 14)
    except Exception:
        n = 0
    for i in range(n):
        el = icons.nth(i)
        try:
            if not el.is_visible():
                continue
            box = el.bounding_box()
            if box and box.get("y", 999) > 320:   # 上部ツールバーだけに絞る
                continue
            el.click(timeout=1500)
        except Exception:
            continue
        page.wait_for_timeout(450)
        if _journal_menu_visible(page):
            return True
        # 誤クリックで別のポップオーバー等が出たら閉じる
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
        except Exception:
            pass
    return False


def _set_modal_daterange(page, start_date: str, end_date: str) -> bool:
    """モーダル内「対象期間」欄に期間を入れる（best-effort。既定＝当日範囲）。"""
    vs, ve = start_date.replace("-", "/"), end_date.replace("-", "/")
    fld = None
    for sel in ['input[value*="~"]', 'input[value*="〜"]',
                'input[placeholder*="~"]', 'input[placeholder*="対象"]',
                'input[type="text"]']:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 8)):
                el = loc.nth(i)
                if el.is_visible():
                    fld = el
                    break
            if fld:
                break
        except Exception:
            continue
    if fld is None:
        return False
    for val in (f"{vs} ~ {ve}", f"{vs} 〜 {ve}", vs):
        try:
            fld.click()
            fld.fill("")
            fld.type(val)
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
            return True
        except Exception:
            continue
    return False


def _dump_modal(page, tag: str) -> None:
    """開いているモーダル/ダイアログの入力欄・ボタンを洗い出して表示（期間設定の目印確定用）。"""
    js = r"""
    () => {
      const cut=(s,n=50)=>(s||'').toString().replace(/\s+/g,' ').trim().slice(0,n);
      const roots=[...document.querySelectorAll('[role=dialog],[class*="modal" i],[class*="Modal"],[class*="dialog" i],[class*="drawer" i]')];
      const root=roots.find(r=>{const b=r.getBoundingClientRect();return b.width>100&&b.height>60;})||document.body;
      const inputs=[...root.querySelectorAll('input,select,textarea')].map(i=>({
        tag:i.tagName,name:i.name,id:i.id,type:i.type,ph:cut(i.placeholder,24),val:cut(i.value,30)}));
      const btns=[...root.querySelectorAll('button,a,[role=button]')].map(b=>({
        text:cut(b.innerText||b.value,40),dis:b.disabled===true,tid:b.getAttribute&&b.getAttribute('data-testid')}))
        .filter(x=>x.text||x.tid);
      return {inputs, btns, n:roots.length};
    }"""
    try:
        info = page.evaluate(js)
    except Exception as e:
        print(f"  [DL診断:{tag}] モーダル構造の取得に失敗: {e}")
        return
    print(f"  [DL診断:{tag}] ダイアログ数={info.get('n')}")
    print(f"  [DL診断:{tag}] 入力欄: {info.get('inputs')}")
    print(f"  [DL診断:{tag}] ボタン: {info.get('btns')}")


def download_csv(page, context, start_date: str | None = None,
                 end_date: str | None = None) -> bytes:
    downloads: list = []
    resp_log: list[str] = []   # csv/journal/export系レスポンスの (status, url, 本文先頭)
    try:
        page.on("download", lambda d: downloads.append(d))
    except Exception:
        pass

    def _on_resp(resp):
        try:
            u = resp.url
            if not any(k in u.lower() for k in ("csv", "journal", "export", "download")):
                return
            body = ""
            try:
                ct = (resp.headers or {}).get("content-type", "")
                if int(resp.status) >= 400 or "json" in ct or "html" in ct:
                    body = resp.text()[:300]
            except Exception:
                body = "(本文取得不可)"
            resp_log.append(f"{resp.status} {u}  ->  {body}")
        except Exception:
            pass
    try:
        page.on("response", _on_resp)
    except Exception:
        pass

    # 1) ダウンロードアイコンのメニューを開く
    if not _open_download_menu(page):
        dump_page(page, "airregi_dl_icon_notfound")
        discover(page)
        raise EtlError("Airレジのダウンロードアイコン（ジャーナル履歴）が開けませんでした。"
                       "debug/airregi_dl_icon_notfound を確認してください。")
    # 2) 「ジャーナル履歴(CSV)」を押す → モーダル表示
    if not _click_by_text(page, JOURNAL_MENU_TEXTS):
        dump_page(page, "airregi_journal_menu_notfound")
        raise EtlError("「ジャーナル履歴(CSV)」が押せませんでした。"
                       "debug/airregi_journal_menu_notfound を確認してください。")
    page.wait_for_timeout(1500)
    _dump_modal(page, "モーダル表示直後")   # 期間欄・ボタンの現状を1回で可視化
    # 3) 対象期間を設定（既定は当日範囲。指定があれば入れる）
    if start_date:
        ok_date = _set_modal_daterange(page, start_date, end_date or start_date)
        print(f"  [DL診断] 期間設定({start_date}〜{end_date or start_date})の実行: {'成功' if ok_date else '失敗（欄が見つからず既定期間のまま）'}")
    # 4) 「ダウンロードの準備を開始する」（サーバ側で非同期生成）
    prep = _click_by_text(page, PREP_TEXTS)
    print(f"  [DL診断] 「準備を開始」ボタン押下: {prep!r}")
    # 5) 生成完了を待って、最新の「ダウンロードする」を押す
    page.wait_for_timeout(8000)   # 新しい準備済みファイルが一覧の先頭に出るのを待つ
    deadline = time.time() + 150
    while time.time() < deadline and not downloads:
        try:
            btn = page.get_by_text("ダウンロードする").first
            if btn and btn.is_visible():
                btn.click()
        except Exception:
            pass
        for _ in range(10):
            if downloads:
                break
            page.wait_for_timeout(500)
        page.wait_for_timeout(2000)
    if downloads:
        path = downloads[0].path()
        if path:
            with open(path, "rb") as fh:
                return fh.read()

    # ここに来た＝UIからのダウンロードが発火しなかった。原因究明のため、
    # モーダルの現状・csv/journal系レスポンス・通信URLを1回で全部出す。
    print("  [DL診断] UIダウンロードが発火せず。以下を確認してください:")
    _dump_modal(page, "ダウンロード不発時")
    print(f"  [DL診断] csv/journal/export系レスポンス（{len(resp_log)}件）:")
    for line in resp_log[-12:]:
        print(f"      {line}")
    log = getattr(page, "_notime_requests", None) or []
    hits = [e for e in log if any(k in e.lower() for k in ("csv", "export", "download", "journal"))]
    print(f"  [DL診断] csv/journal系リクエストURL（{len(hits)}件）:")
    for e in hits[-12:]:
        print(f"      {e}")

    # フォールバック: 通信ログからCSVらしきURLを直接GET（ただし“本物のCSV”のみ採用）。
    for entry in reversed(log):
        m = re.search(r"(https?://\S+)", entry)
        if not m:
            continue
        u = m.group(1)
        if any(k in u.lower() for k in ("csv", "export", "download", "journal")):
            try:
                resp = context.request.get(u)
                if not resp.ok:
                    continue
                body = resp.body()
                head = body[:400].decode("cp932", errors="replace")
                if ("取引No" in head or "取引日" in head) and head.lstrip()[:1] not in ("{", "["):
                    return body        # 本物のCSVだけ返す
            except Exception:
                continue
    dump_page(page, "airregi_csv_download_failed")
    raise EtlError("Airレジの明細CSVをダウンロードできませんでした"
                   "（UIのダウンロードが発火せず、通信ログにも有効なCSVがありません）。\n"
                   "  上の [DL診断] のレスポンス/URL/モーダル構造を確認してください。")


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

    # 失敗時に長時間churnしないよう、Airレジは既定2回まで（環境変数で調整可）。
    try:
        max_try = max(1, int(_env("AIRREGI_RETRIES") or 2))
    except Exception:
        max_try = 2

    last_error: Exception | None = None
    for attempt in range(1, max_try + 1):
        try:
            with browser_page(headless=headless) as (page, context):
                login(page, airid, password)
                select_store(page)   # 複数店アカウント→下北沢を選ぶ
                if _env("AIRREGI_DEBUG"):
                    discover(page)
                try:
                    open_sales(page)
                    text = decode_csv(
                        download_csv(page, context, start_date, end_date))
                    # 取得物がCSVか検証。Airレジがエラー時に返すJSON
                    #   {"results":{"returnCode":"4001","errMsg":"システムエラー"...}}
                    # を「売上0」と誤判定しないよう、CSVでなければ失敗として扱う。
                    head = text.lstrip()[:1000]
                    if head[:1] in ("{", "[") or ("取引No" not in head and "取引日" not in head):
                        raise EtlError(
                            "AirレジのCSVダウンロードに失敗しました"
                            "（CSVではない応答＝ダウンロード導線の変更/システムエラーの可能性）。\n"
                            f"  応答の先頭: {text[:200]!r}")
                    return text
                except Exception:
                    # 失敗時は画面構造をログに残して次の調整に使えるようにする。
                    try:
                        print("  ⚠️ 会計明細/CSV取得で失敗。画面構造を洗い出します（調査用）:")
                        discover(page)
                    except Exception:
                        pass
                    raise
        except Exception as e:
            last_error = e
            if attempt < max_try:
                wait = RETRY_WAIT_SEC * attempt
                print(f"  {attempt}回目失敗（{e}）。{wait}秒待って再試行します...")
                time.sleep(wait)
    raise EtlError(f"AirレジのCSVを{max_try}回試しても取得できませんでした。\n{last_error}")


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
            select_store(page)
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
