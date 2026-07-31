"""
スマレジ（Smaregi）ログイン＆売上データ出力場所 調査プローブ

目的:
  スマレジ管理画面(https://www1.smaregi.jp/control/)にメール＋パスワードでログインし、
  売上（取引/明細）のCSV出力がどこにあるかを突き止める。取り込みはしない。
  → SELFURUGI隠岐など「スマレジ店」の売上自動取得を作るための前段。

必要な環境変数（GitHub Secrets）:
  SMAREGI_ID … ログイン用メールアドレス
  SMAREGI_PW … パスワード

出すもの（Actionsログ）:
  ・ログイン後のURL・ページ見出し
  ・メニュー/リンク/ボタン（ラベルとhref）一覧
  ・download/csv/取引/売上/エクスポート を含む観測URL
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.browser import browser_page  # noqa: E402
from etl.settings import load_dotenv  # noqa: E402

LOGIN_URL = "https://www1.smaregi.jp/control/Main.html"


def main() -> int:
    load_dotenv()
    user = os.environ.get("SMAREGI_ID") or ""
    pw = os.environ.get("SMAREGI_PW") or ""
    if not user or not pw:
        print("SMAREGI_ID / SMAREGI_PW が未登録です。Secretsに登録してください。")
        return 1

    print("===== スマレジ ログイン＆売上出力 調査 =====")
    seen: list[tuple[str, str]] = []
    with browser_page(headless=True) as (page, context):
        page.on("request", lambda req: seen.append((req.method, req.url)))

        print(f"ログイン画面を開く: {LOGIN_URL}")
        page.goto(LOGIN_URL, wait_until="networkidle")
        page.wait_for_timeout(1500)

        # メール＋パスワードを入れてログイン
        filled = False
        for sel in [("メールアドレス", "パスワード")]:
            try:
                page.get_by_placeholder(sel[0]).first.fill(user, timeout=5000)
                page.get_by_placeholder(sel[1]).first.fill(pw, timeout=5000)
                filled = True
            except Exception:
                pass
        if not filled:
            # フォールバック：type属性で
            try:
                page.locator('input[type="email"], input[type="text"]').first.fill(user, timeout=5000)
                page.locator('input[type="password"]').first.fill(pw, timeout=5000)
                filled = True
            except Exception as e:
                print(f"  入力欄が見つかりません: {type(e).__name__}: {e}")

        try:
            page.get_by_role("button", name="ログイン").first.click(timeout=6000)
        except Exception:
            try:
                page.locator('button[type="submit"], input[type="submit"]').first.click(timeout=6000)
            except Exception as e:
                print(f"  ログインボタン押下失敗: {type(e).__name__}: {e}")
        page.wait_for_timeout(4000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        print(f"\nログイン後URL: {page.url}")
        try:
            print(f"タイトル: {page.title()}")
        except Exception:
            pass

        # ログインできたか（パスワード欄が残っていれば失敗の可能性）
        body = ""
        try:
            body = page.content()
        except Exception:
            pass
        if "パスワード" in body and "ログアウト" not in body:
            print("  ⚠️ まだログイン画面の可能性（メール/パスワード or 追加認証を確認）")

        # メニュー/リンク/ボタン一覧
        print("\n----- 画面内リンク/ボタン（ラベル・href） -----")
        try:
            parts = page.evaluate(r"""() => {
              const out=[];
              document.querySelectorAll('a[href],button').forEach(el=>{
                const lbl=(el.innerText||el.getAttribute('aria-label')||'').trim().slice(0,40);
                const href=el.getAttribute('href')||'';
                if(lbl||href) out.push({t:el.tagName.toLowerCase(), lbl, href});
              });
              return out.slice(0,120);
            }""")
            for p in parts:
                print(f"  {p['t']}  {p['lbl']}  {p['href']}")
        except Exception as e:
            print(f"  取得失敗: {type(e).__name__}: {e}")

        # 売上/取引メニューを開いてみる（よくあるパス候補）
        print("\n----- 売上/取引メニューの候補を開く -----")
        for path in ["/control/TransactionList.html", "/control/Transaction.html",
                     "/control/Analysis.html", "/control/SalesList.html",
                     "/control/DailySales.html"]:
            try:
                resp = page.goto("https://www1.smaregi.jp" + path, wait_until="networkidle")
                page.wait_for_timeout(1200)
                head = ""
                try:
                    head = page.title()
                except Exception:
                    pass
                print(f"  {path}  HTTP {resp.status if resp else '?'}  title={head}")
            except Exception as e:
                print(f"  {path}  失敗: {type(e).__name__}: {e}")

        # 観測URL（download/csv/取引/売上/export/analysis）
        print("\n----- 観測URL（download/csv/取引/売上/export/analysis 含む） -----")
        keys = ("download", "csv", "export", "transaction", "sales", "analysis", "daily")
        uniq = []
        for m, u in seen:
            if any(k in u.lower() for k in keys) and (m, u) not in uniq:
                uniq.append((m, u))
        for m, u in uniq[:80]:
            print(f"  {m:4} {u}")

    print("\n===== 調査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
