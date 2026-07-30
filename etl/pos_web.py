"""
汎用POSレジ取得（Airレジ／EZレジ等）— 保存された接続情報でログインしCSVを取る

cashier_fetch.py と同じ考え方（ブラウザでログイン→期間指定→CSV出力）を、
アカウント（接続）ごとの URL・ID・PW を引数で受け取る汎用版にしたもの。

サイトごとに画面が違うので、目印は「候補を上から試す＋文字で探す」方式にし、
うまくいかない時は失敗地点の画面・入力欄・ボタン・通信を debug/ とログに残す
（＝最初の実運用1回で本物の目印が分かり、あとから確定できる。cashierと同じ）。

環境変数で目印を上書きできる（<PREFIX> は AIRREGI / EZREGI 等、pos_type大文字）:
  <PREFIX>_ID_SELECTOR / <PREFIX>_PW_SELECTOR / <PREFIX>_SUBMIT_TEXT
  <PREFIX>_DATE_FROM_SELECTOR / <PREFIX>_DATE_TO_SELECTOR
  <PREFIX>_SEARCH_TEXT / <PREFIX>_CSV_TEXT
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

from .browser import browser_page, dump_page, dump_controls
from .settings import EtlError, RETRIES, RETRY_WAIT_SEC
# cashier_fetch の汎用ヘルパを再利用（重複を避ける）
from .cashier_fetch import (
    _click_by_text, _click_exact, _click_first_text, _confirm_download_modal,
    _first_visible, _force_set_value, decode_csv,
)

# --- 目印の候補（サイト共通で効きやすい順）-------------------------------------
ID_CANDIDATES = ['input[type="email"]', 'input[type="text"]',
                 'input[name*="mail" i]', 'input[name*="user" i]',
                 'input[name*="login" i]', 'input[name*="id" i]']
SUBMIT_TEXTS = ["ログイン", "サインイン", "ログインする", "Login", "Sign in", "次へ"]
DATE_FROM = ['input[name="since"]', 'input[type="date"]',
             'input[name*="start" i]', 'input[name*="from" i]',
             'input[name*="date" i]', 'input[class*="date" i]',
             'input.datepicker', 'input[placeholder*="開始"]',
             'input[placeholder*="日付"]', 'input[placeholder*="YYYY"]']
DATE_TO = ['input[name="until"]', 'input[type="date"]',
           'input[name*="end" i]', 'input[name*="to" i]',
           'input[name*="date" i]', 'input[class*="date" i]',
           'input.datepicker', 'input[placeholder*="終了"]',
           'input[placeholder*="日付"]', 'input[placeholder*="YYYY"]']
SEARCH_TEXTS = ["検索", "絞り込み", "表示", "適用", "この条件で検索", "更新"]
CSV_TEXTS = ["CSV出力(明細)", "CSV出力（明細）", "CSVダウンロード", "CSV出力",
             "CSVエクスポート", "明細CSV", "ダウンロード", "エクスポート", "CSV"]


def _sel(prefix: str, name: str) -> str | None:
    v = os.environ.get(f"{prefix}_{name}", "").strip()
    return v or None


def _login(page, url: str, login_id: str, login_pw: str, prefix: str, label: str) -> None:
    print(f"  {label}: ログインしています… {url}")
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector('input[type="password"]', timeout=30_000)
    except Exception:
        # 既にログイン済み（Cookie/SSO）なら password欄は出ない
        if page.locator('input[type="password"]').count() == 0 and "login" not in page.url.lower():
            print("  （パスワード欄が無い＝既にログイン済みとみなして進みます）")
            return
        dump_page(page, f"{label}_login_notfound")
        dump_controls(page, f"{label}_login_notfound")  # ログイン欄の name もログに出す
        raise EtlError(f"{label}: ログイン画面（パスワード欄）が見つかりませんでした。URL/画面をご確認ください。")

    id_sel = _sel(prefix, "ID_SELECTOR")
    id_field = page.locator(id_sel).first if id_sel else _first_visible(page, ID_CANDIDATES)
    if id_field is not None:
        try:
            id_field.fill(login_id)
        except Exception:
            _force_set_value(page, id_sel or 'input[type="text"]', login_id)
    pw_sel = _sel(prefix, "PW_SELECTOR") or 'input[type="password"]'
    page.fill(pw_sel, login_pw)

    submit_text = _sel(prefix, "SUBMIT_TEXT")
    texts = [submit_text] if submit_text else SUBMIT_TEXTS
    if not _click_by_text(page, texts):
        try:
            page.click('button[type="submit"]')
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
    page.wait_for_timeout(2500)
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass

    # ログイン判定：パスワード欄がまだ見えていたら失敗とみなす
    pw = page.locator('input[type="password"]')
    try:
        still = pw.count() > 0 and pw.first.is_visible()
    except Exception:
        still = False
    if still:
        dump_page(page, f"{label}_login_failed")
        raise EtlError(f"{label}: ログインできませんでした（ID/PW誤り・2段階認証・画面変更など）。debug/ を確認してください。")
    print("  ログイン成功")


def _set_date_range(page, d0: str, d1: str, prefix: str, label: str) -> None:
    print(f"  {label}: 期間を {d0} 〜 {d1} に設定します")
    slash0, slash1 = d0.replace("-", "/"), d1.replace("-", "/")
    from_sel = _sel(prefix, "DATE_FROM_SELECTOR")
    to_sel = _sel(prefix, "DATE_TO_SELECTOR")
    start = page.locator(from_sel).first if from_sel else _first_visible(page, DATE_FROM, nth=0)
    end = (page.locator(to_sel).first if to_sel
           else (_first_visible(page, DATE_TO, nth=1) or _first_visible(page, DATE_TO, nth=0)))
    if start is None:
        # 期間欄が見つからなくても、全件表示のサイトもあるので致命にはしない（診断だけ残す）
        dump_page(page, f"{label}_daterange_notfound")
        dump_controls(page, f"{label}_daterange_notfound")  # 日付欄の name をログに出す
        print("  （期間欄が見つかりませんでした。全件のまま進みます。必要なら <PREFIX>_DATE_FROM_SELECTOR で指定）")
        return
    for field, pair in ((start, (d0, slash0)), (end, (d1, slash1))):
        if field is None:
            continue
        for value in pair:
            try:
                field.fill(value)
                break
            except Exception:
                continue
        try:
            field.press("Escape")
        except Exception:
            pass
    search_text = _sel(prefix, "SEARCH_TEXT")
    texts = [search_text] if search_text else SEARCH_TEXTS
    if _click_exact(page, texts) or _click_by_text(page, texts):
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
    time.sleep(1)


def _download(page, context, prefix: str, label: str) -> bytes:
    downloads: list = []
    popups: list = []

    def _watch(pg):
        try:
            pg.on("download", lambda d: downloads.append(d))
        except Exception:
            pass
    _watch(page)
    context.on("page", lambda pg: (popups.append(pg), _watch(pg)))

    csv_text = _sel(prefix, "CSV_TEXT")
    texts = [csv_text] if csv_text else CSV_TEXTS
    clicked = _click_first_text(page, texts)
    if clicked is None:
        dump_page(page, f"{label}_csv_button_notfound")
        dump_controls(page, f"{label}_csv_button_notfound")  # 隠れたCSVボタン/検索フォームもログに出す
        raise EtlError(f"{label}: 「CSV出力」ボタンが見つかりませんでした。debug/{label}_csv_button_notfound_report.txt を確認してください。")
    _confirm_download_modal(page)   # 形式選択モーダルが出たら中のダウンロードを押す

    deadline = time.time() + 90
    while not downloads and time.time() < deadline:
        page.wait_for_timeout(500)
    if downloads:
        path = downloads[0].path()
        if path:
            return open(path, "rb").read()

    # ダウンロードが発火しない場合：ポップアップのCSV URLを直接取りに行く
    for pg in popups:
        try:
            url = pg.url
        except Exception:
            continue
        if url and any(k in url.lower() for k in ("csv", "export", "download", "report")):
            resp = context.request.get(url, timeout=120_000)
            if resp.ok and resp.body():
                return resp.body()

    dump_page(page, f"{label}_csv_download_failed")
    dump_controls(page, f"{label}_csv_download_failed")
    raise EtlError(f"{label}: CSVダウンロードが完了しませんでした。debug/{label}_csv_download_failed_report.txt の通信記録をご確認ください。")


# --- SIPOS（EZレジ）専用フロー ------------------------------------------------
# 画面：ログイン → 店舗選択（既定で全店舗選択済み）→ 取引照会 → 期間指定 → 検索 → CSV。
# 実機のスクショで確定：取引照会 = /management/transaction/transactionSearch/list、
# 期間は「検索開始日時 / 検索終了日時」= YYYY/MM/DD HH:MM、結果に「CSVダウンロード」。
SIPOS_SEARCH_PATH = "/management/transaction/transactionSearch/list"


def _is_sipos(url: str) -> bool:
    return "sipos.services" in (url or "").lower()


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _sipos_wait(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass


def _sipos_select_all_stores(page, label: str) -> None:
    """店舗選択画面で全店舗を選ぶ。onclickのAJAXでサーバ側セッションに反映されるため、
    見た目がチェック済みでも click で必ずハンドラを発火させ、最終状態をONにする。"""
    # まず現状を診断出力（チェック状況・ボタン・選択中バナー）。
    try:
        info = page.evaluate(r"""() => {
          const q = s => [...document.querySelectorAll(s)];
          const cbs = q('input[type=checkbox]').map(c => ({id:c.id||null,
            cls:(c.className||'').toString().slice(0,50), checked:c.checked}));
          const btns = q('button,a,[role=button],input[type=submit],input[type=button]')
            .map(e => ({tag:e.tagName, text:(e.innerText||e.value||'').trim().slice(0,36),
              href:e.getAttribute&&e.getAttribute('href'),
              onclick:(e.getAttribute&&e.getAttribute('onclick'))?true:null}))
            .filter(b => b.text);
          const m = (document.body.innerText||'').match(/選択中の店舗[^\n]*/);
          return {url:location.href, banner:m?m[0]:'', cbs, btns};
        }""")
        print(f"  [SIPOS店舗選択] url={info.get('url')} / {info.get('banner')}")
        print(f"    checkbox={info.get('cbs')}")
        btns = [b for b in info.get("btns", []) if not str(b.get("href", "")).startswith("#news")]
        print(f"    button/link={btns[:20]}")
    except Exception as e:
        print(f"  （店舗選択の診断に失敗: {e}）")

    # 「全店舗選択」を OFF→ON でトグルして onclick を確実に発火させる。
    try:
        cb = page.locator("#check_all_stores").first
        if cb.count():
            if cb.is_checked():
                cb.click(); page.wait_for_timeout(400)
            cb.click(); page.wait_for_timeout(1500)
    except Exception:
        pass
    # 保険：個別の店舗チェックも未選択があれば ON にする。
    try:
        boxes = page.locator("input.store[type=checkbox]")
        for i in range(min(boxes.count(), 60)):
            b = boxes.nth(i)
            try:
                if not b.is_checked():
                    b.click(); page.wait_for_timeout(200)
            except Exception:
                continue
    except Exception:
        pass
    # 「決定/表示/選択」等の確定ボタンがあれば押す（無い画面もある）。
    _click_by_text(page, ["決定", "この店舗", "選択して表示", "表示する", "適用", "OK"])
    _sipos_wait(page)


def _sipos_set_datetime(page, label_text: str, value: str) -> bool:
    """『検索開始日時』等のラベル直後の入力欄に YYYY/MM/DD HH:MM を入れる。"""
    try:
        lbl = page.get_by_text(label_text, exact=False).first
        inp = lbl.locator("xpath=following::input[1]")
        inp.wait_for(state="visible", timeout=10_000)
    except Exception as e:
        print(f"    日時欄が見つかりません（{label_text}）: {e}")
        return False
    try:
        inp.click(timeout=5_000)
    except Exception:
        pass
    try:
        inp.fill(value, timeout=5_000)
        ok = True
    except Exception:
        # readonly / 日付ピッカーは JS で値を入れてイベントを発火させる
        try:
            h = inp.element_handle()
            page.evaluate(
                "([el,v])=>{try{el.removeAttribute('readonly');}catch(e){}"
                "el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));"
                "el.dispatchEvent(new Event('change',{bubbles:true}));}",
                [h, value])
            ok = True
        except Exception as e:
            print(f"    日時の入力に失敗（{label_text}）: {e}")
            ok = False
    try:
        page.keyboard.press("Escape")   # カレンダーを閉じる
    except Exception:
        pass
    return ok


def _fetch_sipos(page, context, url: str, d0: str, d1: str, label: str) -> bytes:
    """SIPOS：ログイン済みページから 取引照会→期間→検索→CSVダウンロード。"""
    base = _origin(url)
    search_url = base + SIPOS_SEARCH_PATH

    # 1) 店舗選択（サーバ側セッションに反映させる）。ログイン直後は storeSelect にいる想定。
    if "storeselect" not in page.url.lower():
        try:
            page.goto(base + "/management/storeSelect", wait_until="domcontentloaded")
            _sipos_wait(page)
        except Exception:
            pass
    _sipos_select_all_stores(page, label)

    # 2) 取引照会へ。店舗未選択だと storeSelect に戻されるので、その時はもう一度選ぶ。
    print(f"  {label}: 取引照会へ移動します {search_url}")
    page.goto(search_url, wait_until="domcontentloaded")
    _sipos_wait(page)
    if "storeselect" in page.url.lower():
        print("  店舗選択に戻されました。もう一度全店舗を選択して進みます。")
        _sipos_select_all_stores(page, label)
        page.goto(search_url, wait_until="domcontentloaded")
        _sipos_wait(page)

    # 期間（YYYY/MM/DD HH:MM）：開始 00:00 / 終了 23:59
    start_v = f"{d0.replace('-', '/')} 00:00"
    end_v = f"{d1.replace('-', '/')} 23:59"
    print(f"  {label}: 期間を {start_v} 〜 {end_v} に設定します")
    _sipos_set_datetime(page, "検索開始日時", start_v)
    _sipos_set_datetime(page, "検索終了日時", end_v)

    # 検索
    if not (_click_exact(page, ["検索"]) or _click_by_text(page, ["検索"])):
        dump_page(page, f"{label}_sipos_search_notfound")
        dump_controls(page, f"{label}_sipos_search_notfound")
        raise EtlError(f"{label}: 取引照会の「検索」ボタンが見つかりませんでした。")
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    # CSVダウンロード（別タブで開く場合にも備えて download を拾う）
    downloads: list = []
    page.on("download", lambda d: downloads.append(d))
    context.on("page", lambda pg: pg.on("download", lambda d: downloads.append(d)))

    if _click_first_text(page, ["CSVダウンロード", "CSV出力", "CSVエクスポート", "CSV"]) is None:
        dump_page(page, f"{label}_sipos_csv_notfound")
        dump_controls(page, f"{label}_sipos_csv_notfound")
        raise EtlError(f"{label}: 「CSVダウンロード」ボタンが見つかりませんでした。"
                       "（検索結果が0件だと出ないことがあります）")
    _confirm_download_modal(page)

    deadline = time.time() + 90
    while not downloads and time.time() < deadline:
        page.wait_for_timeout(500)
    if downloads:
        path = downloads[0].path()
        if path:
            data = open(path, "rb").read()
            print(f"  {label}: CSVを取得しました（{len(data)} bytes）")
            return data
    dump_page(page, f"{label}_sipos_download_failed")
    dump_controls(page, f"{label}_sipos_download_failed")
    raise EtlError(f"{label}: CSVダウンロードが完了しませんでした。debug/ の通信記録をご確認ください。")


def fetch(url: str, login_id: str, login_pw: str, business_date: str,
          pos_type: str, label: str = "pos", headless: bool = True,
          end_date: str | None = None) -> str:
    """接続情報でログイン→期間指定→CSV出力し、CSV文字列を返す。失敗時は数回試す。"""
    if not url:
        raise EtlError(f"{label}: URLが未登録です（管理画面のレジ接続でURLを設定してください）。")
    d1 = end_date or business_date
    prefix = (pos_type or "pos").upper()
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with browser_page(headless=headless) as (page, context):
                _login(page, url, login_id or "", login_pw or "", prefix, label)
                if _is_sipos(url):
                    return decode_csv(_fetch_sipos(page, context, url, business_date, d1, label))
                _set_date_range(page, business_date, d1, prefix, label)
                return decode_csv(_download(page, context, prefix, label))
        except Exception as e:
            last = e
            if attempt < RETRIES:
                wait = RETRY_WAIT_SEC * attempt
                print(f"  {label}: {attempt}回目失敗（{e}）。{wait}秒待って再試行します…")
                time.sleep(wait)
    raise EtlError(f"{label}: CSVを{RETRIES}回試しても取得できませんでした。\n{last}")
