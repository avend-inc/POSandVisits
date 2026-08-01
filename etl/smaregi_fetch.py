"""
スマレジ（Smaregi）から売上データを取ってくる（SELFURUGI隠岐など「スマレジ店」用）

【実データで確認済みの仕様（2026-08-01）】
  ログイン : https://www1.smaregi.jp/control/Main.html にメール＋パスワードで。
  売上データ: AJAXゲートウェイをログイン状態のまま叩く（JSONが返る）。
    GET https://www1.smaregi.jp/services/ajax/gateway.php
        ?service=AjaxMainService&method=<method>
        &params={"viewMode":"1","searchDate":"YYYY/MM/DD"}
    ・getSalesInfo       … その日の売上サマリー（total=税込, totalExcludTaxNum=税抜,
                            transactionCount=取引数, amount=点数, taxRate）
    ・getSalesByCategory … カテゴリ別（categoryName, categoryTotal=税込, categoryCount=点数）

  ※アカウントは対象店（隠岐）にひも付く前提（店舗選択メソッドは未提供）。
"""
from __future__ import annotations

import json
import time

from .browser import browser_page
from .settings import EtlError, RETRIES, RETRY_WAIT_SEC

LOGIN_URL = "https://www1.smaregi.jp/control/Main.html"
GATEWAY = "https://www1.smaregi.jp/services/ajax/gateway.php"


def login(page, user: str, password: str) -> None:
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.wait_for_timeout(1200)
    try:
        page.get_by_placeholder("メールアドレス").first.fill(user, timeout=6000)
        page.get_by_placeholder("パスワード").first.fill(password, timeout=6000)
    except Exception:
        page.locator('input[type="email"],input[type="text"]').first.fill(user, timeout=6000)
        page.locator('input[type="password"]').first.fill(password, timeout=6000)
    try:
        page.get_by_role("button", name="ログイン").first.click(timeout=8000)
    except Exception:
        page.locator('button[type="submit"],input[type="submit"]').first.click(timeout=8000)
    page.wait_for_timeout(3500)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    body = ""
    try:
        body = page.content()
    except Exception:
        pass
    if "パスワード" in body and "ログアウト" not in body:
        raise EtlError("スマレジのログインに失敗した可能性があります（メール/パスワードを確認）。")


def _call(context, method: str, params: dict):
    resp = context.request.get(GATEWAY, params={
        "service": "AjaxMainService", "method": method,
        "params": json.dumps(params, ensure_ascii=False),
    }, timeout=40_000)
    if not resp.ok:
        raise EtlError(f"スマレジAPI {method} 失敗（HTTP {resp.status}）")
    obj = json.loads(resp.body().decode("utf-8", errors="replace"))
    if obj.get("status") != "success":
        raise EtlError(f"スマレジAPI {method} がエラー: {obj.get('message') or obj}")
    return obj.get("data")


def fetch_day(context, date_slash: str) -> dict:
    """date_slash='YYYY/MM/DD'。その日の {info, categories} を返す。"""
    info = _call(context, "getSalesInfo", {"viewMode": "1", "searchDate": date_slash})
    cats = _call(context, "getSalesByCategory", {"viewMode": "1", "searchDate": date_slash})
    return {"info": info or {}, "categories": cats or []}


def fetch_range(start: str, end: str, user: str, password: str,
                headless: bool = True) -> dict[str, dict]:
    """
    start / end = 'YYYY-MM-DD'。期間の各日について {info, categories} を集める。
    戻り値: {'YYYY-MM-DD': {info, categories}, ...}（売上0の日も入る）
    """
    from datetime import date as _date, timedelta

    def _d(s):
        y, m, d = map(int, s.split("-"))
        return _date(y, m, d)

    days = []
    cur, last = _d(start), _d(end)
    while cur <= last:
        days.append(cur)
        cur = cur + timedelta(days=1)

    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            out: dict[str, dict] = {}
            with browser_page(headless=headless) as (page, context):
                login(page, user, password)
                for d in days:
                    iso = d.isoformat()
                    slash = iso.replace("-", "/")
                    try:
                        out[iso] = fetch_day(context, slash)
                    except Exception as e:
                        print(f"  【スマレジ {iso}】取得失敗: {e}")
            return out
        except Exception as e:
            last_error = e
            if attempt < RETRIES:
                wait = RETRY_WAIT_SEC * attempt
                print(f"  スマレジ {attempt}回目失敗（{e}）。{wait}秒待って再試行...")
                time.sleep(wait)
    raise EtlError(f"スマレジを{RETRIES}回試しても取得できませんでした。\n{last_error}")
