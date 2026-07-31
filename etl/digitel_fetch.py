"""
デジテールストアから来店（入店）数CSVを取ってくる

【実データで確認済みの仕様（2026-07-22 に実際のサイトで確認）】
  ログイン : POST https://dashboard.digitail-tech.com/auth/login
             入力欄は username / password の2つだけ。
             ⚠️ メールアドレスではなく「ユーザー名」。
  CSV取得 : GET /{店舗スラッグ}/kpi/visits/download?interval=day&from=...&to=...
             ダウンロードボタンの正体はただのリンク。
  文字コード : UTF-8（BOM付き）
  中身 : 日付,無人入店/解錠数,無人常連来店数,無人購買回数,無人購買率 (%)

【なぜ Playwright を使うか】
  ログイン自体はブラウザ無しでもできるが、
  cashier と手順をそろえた方が分かりやすく、
  サイトがJavaScriptログインに変わっても壊れないため、
  ブラウザでログイン → そのログイン状態のままCSVのURLを取りに行く、にしている。
"""
from __future__ import annotations

import re
import time

from .browser import browser_page, dump_page
from .settings import (
    DIGITEL_BASE_URL,
    DIGITEL_EXPECTED_HEADER,
    DIGITEL_LOGIN_URL,
    EtlError,
    RETRIES,
    RETRY_WAIT_SEC,
    require_env,
)


def login(page, user: str | None = None, password: str | None = None) -> None:
    # 2アカウント（NOTIME / SELFURUGI）対応のため、認証情報は引数でも渡せる。
    # 省略時は従来どおり DIGITAIL_ID / DIGITAIL_PW を使う。
    user = user or require_env("DIGITAIL_ID", "デジテールのユーザー名（メールアドレスではない）")
    password = password or require_env("DIGITAIL_PW", "デジテールのパスワード")

    print("デジテールにログインしています...")
    page.goto(DIGITEL_LOGIN_URL, wait_until="domcontentloaded")

    # セレクタは意味依存で書く。
    # ⚠️ id は «R35»-username のようにReactが毎回作り直すので絶対に使わない。
    page.wait_for_selector('input[name="username"]', timeout=45_000)
    page.fill('input[name="username"]', user)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')

    try:
        page.wait_for_load_state("networkidle", timeout=45_000)
    except Exception:
        pass

    if "/auth/login" in page.url:
        dump_page(page, "digitel_login_failed")
        raise EtlError(
            "デジテールにログインできませんでした。\n"
            "  よくある原因:\n"
            "    ・DIGITAIL_ID / DIGITAIL_PW が違う\n"
            "      （メールアドレスではなく「ユーザー名」です）\n"
            "    ・パスワードが変更された／アカウントがロックされている\n"
            f"  → まずご自分のブラウザで {DIGITEL_LOGIN_URL} に入れるか確かめてください。"
        )
    print("  ログイン成功")


def discover_stores(page) -> dict[str, str]:
    """
    ログインしたアカウントが見られる“全店舗”とスラッグを、手入力なしで拾う。

    デジテールの管理画面は Remix（React）で、店舗一覧が画面のHTMLの中に
    「"NOTIME鎌ヶ谷店","notime_kamagaya"」のように 名前・スラッグの並びで
    埋め込まれている（実データで確認済み）。ここではその並びを機械的に拾う。

    戻り値: {"NOTIME鎌ヶ谷店": "notime_kamagaya", ...}  ※本部などは除く
    """
    page.goto(DIGITEL_BASE_URL + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    # HTML内は \" とエスケープされていることがあるので素の " に戻してから拾う
    html = page.content().replace('\\"', '"')

    # 店名（NOTIME / SELFURUGI で始まる）のすぐ後ろに スラッグ（英小文字＋_）が続く並び
    pairs = re.findall(
        r'"((?:NOTIME|SELFURUGI)[^"]{0,30}?)"\s*,\s*"([a-z][a-z0-9_]{2,})"', html)

    out: dict[str, str] = {}
    for name, slug in pairs:
        name = name.strip()
        if not name or "本部" in name:
            continue
        out.setdefault(name, slug)
    return out


def fetch_store_csv(context, slug: str, start: str, end: str) -> str:
    """
    ログイン済みの状態でCSVのURLを取りに行く。
    ブラウザのcookieをそのまま使うので、ログインが引き継がれる。
    """
    url = f"{DIGITEL_BASE_URL}/{slug}/kpi/visits/download"
    resp = context.request.get(
        url, params={"interval": "day", "from": start, "to": end}, timeout=60_000
    )
    if not resp.ok:
        raise EtlError(f"CSVの取得に失敗しました（HTTP {resp.status}） URL: {url}")

    text = resp.body().decode("utf-8-sig")
    if not text.lstrip().startswith(DIGITEL_EXPECTED_HEADER[0]):
        raise EtlError(
            "CSVではないものが返ってきました（ログインが切れた可能性があります）。\n"
            f"  先頭: {text[:120]!r}"
        )
    return text


def fetch(slugs: dict[str, str], start: str, end: str,
          headless: bool = True) -> dict[str, str]:
    """
    店舗ごとのCSVをまとめて取ってくる。

    引数:
        slugs : {"山形": "notime_yamagata", ...}
        start / end : YYYY-MM-DD（同じ日にすればその1日ぶん）
    戻り値:
        {"山形": "CSVの中身", ...}   ※取れなかった店舗は入らない
    """
    last_error: Exception | None = None

    for attempt in range(1, RETRIES + 1):
        try:
            with browser_page(headless=headless) as (page, context):
                login(page)
                results: dict[str, str] = {}
                errors: list[str] = []
                for store_name, slug in slugs.items():
                    try:
                        results[store_name] = fetch_store_csv(context, slug, start, end)
                        print(f"  【{store_name}】CSV取得OK")
                    except Exception as e:
                        print(f"  【{store_name}】❌ {e}")
                        errors.append(f"{store_name}: {e}")

                if not results:
                    raise EtlError("どの店舗のCSVも取得できませんでした。\n  " +
                                   "\n  ".join(errors))
                return results

        except Exception as e:
            last_error = e
            if attempt < RETRIES:
                wait = RETRY_WAIT_SEC * attempt
                print(f"  {attempt}回目失敗（{e}）。{wait}秒待って再試行します...")
                time.sleep(wait)

    raise EtlError(f"デジテールのCSVを{RETRIES}回試しても取得できませんでした。\n{last_error}")


def fetch_all(start: str, end: str, headless: bool = True,
              user: str | None = None, password: str | None = None) -> dict[str, dict]:
    """
    ログインしたアカウントで“見える全店舗”を自動で見つけて、それぞれのCSVを取ってくる。

    引数:
        start / end : YYYY-MM-DD（同じ日にすればその1日ぶん）
        user / password : 省略時は DIGITAIL_ID / DIGITAIL_PW（NOTIMEアカウント）
    戻り値:
        {"NOTIME鎌ヶ谷店": {"slug": "notime_kamagaya", "csv": "..."}, ...}
        ※CSVが取れなかった店舗は入らない
    """
    last_error: Exception | None = None

    for attempt in range(1, RETRIES + 1):
        try:
            with browser_page(headless=headless) as (page, context):
                login(page, user, password)

                stores = discover_stores(page)
                if not stores:
                    dump_page(page, "digitel_no_stores")
                    raise EtlError(
                        "デジテールの店舗一覧を画面から取得できませんでした。\n"
                        "  （管理画面の作りが変わった可能性があります）"
                    )
                print(f"  このアカウントで見える店舗: {len(stores)}件 "
                      f"{list(stores.keys())}")

                results: dict[str, dict] = {}
                for store_name, slug in stores.items():
                    try:
                        csv = fetch_store_csv(context, slug, start, end)
                        results[store_name] = {"slug": slug, "csv": csv}
                        print(f"  【{store_name}】CSV取得OK（{slug}）")
                    except Exception as e:
                        print(f"  【{store_name}】❌ {e}")

                if not results:
                    raise EtlError("見つかった全店舗でCSVの取得に失敗しました。")
                return results

        except Exception as e:
            last_error = e
            if attempt < RETRIES:
                wait = RETRY_WAIT_SEC * attempt
                print(f"  {attempt}回目失敗（{e}）。{wait}秒待って再試行します...")
                time.sleep(wait)

    raise EtlError(f"デジテールを{RETRIES}回試しても取得できませんでした。\n{last_error}")
