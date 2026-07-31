"""
デジテール「店舗一覧の取り方」調査用プローブ

目的: ログイン後にアカウントが見られる“全店舗”とそのスラッグを、手入力せず
自動で拾うための「店舗一覧の在りか（API or 画面のリンク）」を突き止める。

使い方（GitHub Actions「デジテール店舗一覧の調査」から）:
  python -m tools.digitel_probe            # NOTIMEアカウント（DIGITAIL_ID/PW）
  python -m tools.digitel_probe --sf       # SELFURUGIアカウント（DIGITAIL_SF_ID/PW）

出すもの（Actionsログ）:
  ・ログイン後に管理画面が叩いた通信（API）一覧 … 店舗一覧APIを見つける材料
  ・画面内のリンク（href）とスラッグらしき文字 … リンクから拾える場合の材料
  ・候補APIを実際に叩いた応答の先頭 … JSONで店舗一覧が返るかの確認
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl import digitel_fetch  # noqa: E402
from etl.browser import browser_page, dump_page  # noqa: E402
from etl.settings import DIGITEL_BASE_URL, load_dotenv  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sf", action="store_true", help="SELFURUGIアカウントで調べる")
    args = ap.parse_args()
    load_dotenv()

    if args.sf:
        user = os.environ.get("DIGITAIL_SF_ID") or ""
        pw = os.environ.get("DIGITAIL_SF_PW") or ""
        label = "SELFURUGI"
        if not user or not pw:
            print("DIGITAIL_SF_ID / DIGITAIL_SF_PW が未登録です。先に登録してください。")
            return 1
    else:
        user = pw = None      # 既定の DIGITAIL_ID / DIGITAIL_PW を使う
        label = "NOTIME"

    print(f"===== デジテール店舗一覧の調査（{label}アカウント）=====")
    with browser_page(headless=True) as (page, context):
        digitel_fetch.login(page, user, pw)

        # ダッシュボードのトップへ（SPAが店舗一覧を読みにいく想定）。
        try:
            page.goto(DIGITEL_BASE_URL + "/", wait_until="networkidle")
        except Exception:
            pass
        page.wait_for_timeout(2500)

        # 1) 管理画面が叩いた通信のうち、店舗一覧っぽいAPIを洗い出す。
        reqs = getattr(page, "_notime_requests", []) or []
        apis = [r for r in reqs if any(k in r.lower()
                for k in ("api", "store", "shop", "kpi", "list", "me", "account", "org", "tenant"))]
        print("\n----- ログイン後の通信（店舗一覧APIの候補）-----")
        for r in dict.fromkeys(apis):
            print(f"  {r}")

        # 2) 画面内リンク（slug らしき文字を含むもの）。
        try:
            links = page.evaluate(
                r"""() => [...document.querySelectorAll('a[href]')].map(a => ({
                    t:(a.innerText||'').trim().replace(/\s+/g,' ').slice(0,30),
                    href:a.getAttribute('href')})) """)
        except Exception:
            links = []
        print("\n----- 画面内リンク（店舗スラッグの候補）-----")
        for l in [x for x in links if x.get("href") and "/kpi" in x["href"]
                  or (x.get("href", "").count("/") >= 1 and x.get("t"))][:60]:
            print(f"  {l}")

        # 3) 候補APIを実際に叩いて、JSONで店舗一覧が返るか見る。
        cand = ["/api/stores", "/api/user/stores", "/api/me", "/api/account",
                "/api/v1/stores", "/api/organizations", "/api/shops", "/api/kpi/stores",
                "/stores", "/api/users/me", "/api/tenants"]
        print("\n----- 候補APIの応答（先頭のみ）-----")
        for ep in cand:
            try:
                r = context.request.get(DIGITEL_BASE_URL + ep, timeout=20_000)
                body = ""
                try:
                    body = r.text()[:400]
                except Exception:
                    body = "(本文取得不可)"
                print(f"  GET {ep} -> {r.status}  {body!r}")
            except Exception as e:
                print(f"  GET {ep} -> ERR {e}")

        dump_page(page, f"digitel_dashboard_{label}")
    print("\n===== 調査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
