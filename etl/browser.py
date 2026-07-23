"""
ブラウザ操作（Playwright）の共通部分

cashier もデジテールも「ブラウザでログインしないとCSVが取れない」ので、
ヘッドレス（画面を出さない）Chromium を使う。

【失敗したときに必ずやること】
  ・その瞬間の画面のスクリーンショットとHTMLを debug/ に保存する
  → 画面が変わって動かなくなったとき、原因がすぐ分かる。
    GitHub Actions では、この debug/ を成果物としてダウンロードできるようにしている。
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from .settings import BROWSER_TIMEOUT_MS, DEBUG_DIR, EtlError


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        raise EtlError(
            "Playwright（ブラウザ操作の部品）が入っていません。\n"
            "  → 次の2つを順番に実行してください:\n"
            "       pip install -r requirements.txt\n"
            "       python -m playwright install chromium"
        )


@contextmanager
def browser_page(headless: bool = True, accept_downloads: bool = True):
    """
    ブラウザを開いて1ページ渡す。使い終わったら自動で閉じる。

    使い方:
        with browser_page() as (page, context):
            page.goto("https://...")
    """
    sync_playwright = _import_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context(
            accept_downloads=accept_downloads,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1440, "height": 900},
        )
        context.set_default_timeout(BROWSER_TIMEOUT_MS)
        page = context.new_page()
        try:
            yield page, context
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()


def dump_page(page, label: str) -> Path | None:
    """
    今の画面をスクリーンショット＋HTMLで保存する。
    エラー調査用。失敗しても本体は止めない。
    """
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        png = DEBUG_DIR / f"{label}.png"
        html = DEBUG_DIR / f"{label}.html"
        page.screenshot(path=str(png), full_page=True)
        html.write_text(page.content(), encoding="utf-8")
        print(f"  🔍 調査用に画面を保存しました: {png} / {html}")
        return png
    except Exception as e:
        print(f"  （画面の保存はできませんでした: {e}）")
        return None
