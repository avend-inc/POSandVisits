"""
デジテール「売上CSVの取り方」調査用プローブ（伊予松前ほか）

目的: 来店(visits)と同じ /{slug}/kpi/●●/download の ●● が、売上では何なのか、
      そしてCSVの列（見出し）が何なのかを、実データで確定する。
      ※取り込みはしない。エンドポイントの当たり付けと中身確認だけ。

使い方（GitHub Actions「デジテール売上CSVの調査」から）:
  python -m tools.digitel_sales_probe                       # 既定: 伊予松前(notime_masakicho)
  python -m tools.digitel_sales_probe --slug notime_waseda  # 別店
  python -m tools.digitel_sales_probe --sf --slug selffurugi_utsunomiya

出すもの（Actionsログ）:
  ・ダッシュボードが叩いた通信のうち download / kpi を含むURL一覧（売上の在りか）
  ・画面内リンク(href)のうち download を含むもの
  ・候補エンドポイントを実際にGETした結果（HTTPコードとCSV先頭数行）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl import digitel_fetch  # noqa: E402
from etl.browser import browser_page, dump_page  # noqa: E402
from etl.settings import DIGITEL_BASE_URL, load_dotenv  # noqa: E402

# 来店は visits。売上はこの辺りが候補（実際に叩いて確かめる）。
CANDIDATE_METRICS = [
    "visits", "sales", "revenue", "uriage", "amount", "amounts",
    "transactions", "transaction", "orders", "order", "purchase",
    "purchases", "payment", "payments",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sf", action="store_true", help="SELFURUGIアカウントで調べる")
    ap.add_argument("--slug", default="notime_masakicho", help="調べる店舗スラッグ")
    ap.add_argument("--from", dest="d_from", default="2026-07-24")
    ap.add_argument("--to", dest="d_to", default="2026-07-30")
    args = ap.parse_args()
    load_dotenv()

    if args.sf:
        user = os.environ.get("DIGITAIL_SF_ID") or ""
        pw = os.environ.get("DIGITAIL_SF_PW") or ""
        if not user or not pw:
            print("DIGITAIL_SF_ID / DIGITAIL_SF_PW が未登録です。"); return 1
        label = "SELFURUGI"
    else:
        user = pw = None
        label = "NOTIME"

    slug = args.slug
    print(f"===== デジテール 売上CSV 調査（{label} / slug={slug}）=====")
    print(f"  期間: {args.d_from} 〜 {args.d_to}")

    seen_urls: list[str] = []
    with browser_page(headless=True) as (page, context):
        # ログイン後にダッシュボードが叩く通信を記録（download/kpi を含むものだけ）
        def on_request(req):
            u = req.url
            if "/download" in u or "/kpi/" in u:
                if u not in seen_urls:
                    seen_urls.append(u)
        page.on("request", on_request)

        digitel_fetch.login(page, user, pw)

        # トップ → 可能なら店舗ページへ（SPAなのでどちらでも通信は拾える）
        for path in ["/", f"/{slug}", f"/{slug}/kpi"]:
            try:
                page.goto(DIGITEL_BASE_URL + path, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
            except Exception:
                pass

        print("\n----- 通信で見えた download / kpi URL -----")
        if seen_urls:
            for u in seen_urls[:40]:
                print("  " + u)
        else:
            print("  （該当なし）")

        # 画面内リンクのうち download を含むもの
        print("\n----- 画面内リンク(href) の download -----")
        try:
            hrefs = page.evaluate(
                r"""() => Array.from(document.querySelectorAll('a[href]'))
                        .map(a=>a.getAttribute('href'))
                        .filter(h=>h && h.toLowerCase().includes('download'))""")
        except Exception:
            hrefs = []
        for h in (hrefs or [])[:40]:
            print("  " + h)
        if not hrefs:
            print("  （該当なし）")

        # 候補エンドポイントを実際にGETして中身を確認
        print("\n----- 候補エンドポイントを実際にGET -----")
        params = {"interval": "day", "from": args.d_from, "to": args.d_to}
        for m in CANDIDATE_METRICS:
            url = f"{DIGITEL_BASE_URL}/{slug}/kpi/{m}/download"
            try:
                resp = context.request.get(url, params=params, timeout=30_000)
                body = resp.body().decode("utf-8-sig", errors="replace")
                head = body.replace("\r", "")[:220].replace("\n", " / ")
                ok = "✅" if resp.ok else "  "
                print(f"  {ok} [{resp.status}] kpi/{m}/download  先頭: {head!r}")
            except Exception as e:
                print(f"     [ERR] kpi/{m}/download  {type(e).__name__}: {e}")

        dump_page(page, f"digitel_sales_probe_{slug}")
    print("\n===== 調査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
