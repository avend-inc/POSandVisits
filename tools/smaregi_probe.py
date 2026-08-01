"""
スマレジ（Smaregi）売上API 調査プローブ（第2段）

ログイン後、スマレジの AJAX ゲートウェイを直接叩いて、隠岐の売上データの
JSON構造を確定する（取り込みはしない）。

観測済みエンドポイント:
  https://www1.smaregi.jp/services/ajax/gateway.php
    ?service=AjaxMainService&method=getSalesInfo|getSalesByProduct|getSalesByCategory
    &params={"viewMode":"1","searchDate":"YYYY/MM/DD"}

必要なSecrets: SMAREGI_ID（メール）/ SMAREGI_PW（パスワード）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.browser import browser_page  # noqa: E402
from etl.settings import load_dotenv  # noqa: E402

LOGIN_URL = "https://www1.smaregi.jp/control/Main.html"
GATEWAY = "https://www1.smaregi.jp/services/ajax/gateway.php"


def _login(page, user, pw):
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.wait_for_timeout(1200)
    try:
        page.get_by_placeholder("メールアドレス").first.fill(user, timeout=5000)
        page.get_by_placeholder("パスワード").first.fill(pw, timeout=5000)
    except Exception:
        page.locator('input[type="email"],input[type="text"]').first.fill(user, timeout=5000)
        page.locator('input[type="password"]').first.fill(pw, timeout=5000)
    try:
        page.get_by_role("button", name="ログイン").first.click(timeout=6000)
    except Exception:
        page.locator('button[type="submit"],input[type="submit"]').first.click(timeout=6000)
    page.wait_for_timeout(3500)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass


def _call(context, method, params):
    resp = context.request.get(GATEWAY, params={
        "service": "AjaxMainService", "method": method,
        "params": json.dumps(params, ensure_ascii=False),
    }, timeout=40_000)
    body = resp.body().decode("utf-8", errors="replace")
    return resp.status, body


def main() -> int:
    load_dotenv()
    user = os.environ.get("SMAREGI_ID") or ""
    pw = os.environ.get("SMAREGI_PW") or ""
    if not user or not pw:
        print("SMAREGI_ID / SMAREGI_PW が未登録です。"); return 1

    date = os.environ.get("PROBE_DATE", "2026/07/29")
    print(f"===== スマレジ 売上API 調査（searchDate={date}）=====")
    with browser_page(headless=True) as (page, context):
        _login(page, user, pw)
        print(f"ログイン後URL: {page.url}")

        # 店舗一覧の当たり付け（候補メソッドをいくつか試す）
        print("\n----- 店舗一覧の候補メソッド -----")
        for m in ["getStoreList", "getStores", "getStoreInfo", "getShopList"]:
            try:
                st, body = _call(context, m, {})
                print(f"  {m}: HTTP {st}  {body[:400]}")
            except Exception as e:
                print(f"  {m}: ERR {type(e).__name__}: {e}")

        # 本命：売上サマリー・商品別・カテゴリ別（1日）
        for m in ["getSalesInfo", "getSalesByProduct", "getSalesByCategory"]:
            print(f"\n----- {m}（{date}） -----")
            try:
                st, body = _call(context, m, {"viewMode": "1", "searchDate": date})
                print(f"  HTTP {st}")
                try:
                    obj = json.loads(body)
                    if m == "getSalesInfo":
                        # 取引数・客数のフィールド名を確定したいので全キー＆全文を出す
                        d = obj.get("data")
                        if isinstance(d, dict):
                            print("  data keys:", list(d.keys()))
                        print(json.dumps(obj, ensure_ascii=False, indent=2)[:6000])
                    else:
                        print(json.dumps(obj, ensure_ascii=False, indent=2)[:2500])
                except Exception:
                    print(body[:3000])
            except Exception as e:
                print(f"  ERR {type(e).__name__}: {e}")

        # 期間指定が効くかの当たり付け（dateFrom/dateTo）
        print("\n----- getSalesByProduct（期間指定の当たり付け） -----")
        try:
            st, body = _call(context, "getSalesByProduct",
                             {"viewMode": "1", "dateFrom": "2026/07/28", "dateTo": date})
            print(f"  HTTP {st}  {body[:800]}")
        except Exception as e:
            print(f"  ERR {type(e).__name__}: {e}")

    print("\n===== 調査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
