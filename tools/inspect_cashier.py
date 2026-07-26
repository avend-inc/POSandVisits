"""
cashier の取引一覧画面を調べるための道具（1回だけ使う）

【何のため？】
  cashier の「期間の入力欄」と「CSVダウンロードボタン」の正確な目印は、
  ログインした後の画面を見ないと分かりません。
  でも、パスワードを他人（AI）に渡す必要はありません。

【使い方】
  1. このフォルダで次を実行します:

         python tools/inspect_cashier.py

  2. ブラウザの窓が開きます。**ご自分で** メールアドレスとパスワードを入力して
     ログインし、「取引」の一覧画面を表示してください。
     （このスクリプトは入力内容を一切保存しません）

  3. 一覧画面が出たら、黒い画面（コマンドプロンプト）で Enter を押します。

  4. debug/ フォルダに次のファイルができます:
         cashier_trade.png    … 画面の写真
         cashier_trade.html   … 画面の中身
         cashier_report.txt   … 入力欄・ボタンの一覧（ここが本命）

  5. debug/cashier_report.txt の中身をコピーして貼り付けてもらえれば、
     期間指定とCSVボタンの目印を確定できます。

  ⚠️ debug/ は .gitignore 済みなので、GitHubには上がりません。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl.browser import format_scan_report, scan_page  # noqa: E402
from etl.settings import CASHIER_TRADE_URL, DEBUG_DIR  # noqa: E402


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright が入っていません。先に次を実行してください:\n"
              "  pip install -r requirements.txt\n"
              "  python -m playwright install chromium")
        return 1

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    requests_log: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo",
                                      accept_downloads=True)
        page = context.new_page()

        # CSVボタンを押したときに、裏でどのURLを叩いているかを記録する
        page.on("request", lambda r: requests_log.append(f"{r.method} {r.url}"))
        page.on("download", lambda d: requests_log.append(f"DOWNLOAD {d.url}"))

        page.goto(CASHIER_TRADE_URL)

        print("=" * 64)
        print("ブラウザが開きました。")
        print("  1) ご自分でログインしてください（入力内容は保存されません）")
        print("  2) 『取引』の一覧画面を出してください")
        print("  3) できれば、日付の期間を1日だけに設定し、")
        print("     CSVダウンロードのボタンも一度押してみてください")
        print("  4) 終わったら、この画面で Enter を押してください")
        print("=" * 64)
        input(">>> 準備ができたら Enter: ")

        info = scan_page(page)

        (DEBUG_DIR / "cashier_trade.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(DEBUG_DIR / "cashier_trade.png"), full_page=True)

        report = format_scan_report(info, requests_log)
        (DEBUG_DIR / "cashier_report.txt").write_text(report, encoding="utf-8")

        browser.close()

    print(f"\n✅ 保存しました → {DEBUG_DIR}")
    print("   cashier_report.txt の中身を教えてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
