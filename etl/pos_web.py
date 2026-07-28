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

from .browser import browser_page, dump_page
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
    raise EtlError(f"{label}: CSVダウンロードが完了しませんでした。debug/{label}_csv_download_failed_report.txt の通信記録をご確認ください。")


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
                _set_date_range(page, business_date, d1, prefix, label)
                return decode_csv(_download(page, context, prefix, label))
        except Exception as e:
            last = e
            if attempt < RETRIES:
                wait = RETRY_WAIT_SEC * attempt
                print(f"  {label}: {attempt}回目失敗（{e}）。{wait}秒待って再試行します…")
                time.sleep(wait)
    raise EtlError(f"{label}: CSVを{RETRIES}回試しても取得できませんでした。\n{last}")
