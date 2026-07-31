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
    print(f"===== デジテール 売上 調査（{label} / slug={slug}）=====")
    print(f"  期間: {args.d_from} 〜 {args.d_to}")

    seen: list[str] = []
    with browser_page(headless=True) as (page, context):
        # この店に関する通信を全部記録（.data / download / kpi を見つける）
        def on_request(req):
            u = req.url
            if slug in u and u not in seen:
                seen.append(u)
        page.on("request", on_request)

        digitel_fetch.login(page, user, pw)

        # 売上ページを実際に開く（人と同じ動き）。日別も見るため interval=day も。
        for path in [f"/{slug}/kpi/sales",
                     f"/{slug}/kpi/sales?interval=day",
                     f"/{slug}/kpi/sales?interval=day&from={args.d_from}&to={args.d_to}"]:
            try:
                page.goto(DIGITEL_BASE_URL + path, wait_until="networkidle")
                page.wait_for_timeout(2500)
            except Exception:
                pass

        print("\n----- この店に関する通信URL（.data / download を探す）-----")
        for u in seen[:60]:
            mark = "★" if (".data" in u or "download" in u) else " "
            print(f"  {mark} {u}")

        # 売上ページ内の「ダウンロード/CSV」ボタン・リンク
        print("\n----- 売上ページ内の ダウンロード/CSV ボタン・リンク -----")
        try:
            hits = page.evaluate(r"""() => {
              const out=[];
              document.querySelectorAll('a[href],button').forEach(el=>{
                const t=(el.innerText||'')+' '+(el.getAttribute('href')||'')+' '+(el.getAttribute('aria-label')||'');
                if(/download|csv|ダウンロード|エクスポート|出力/i.test(t))
                  out.push({tag:el.tagName, text:(el.innerText||'').trim().slice(0,30), href:el.getAttribute('href')||''});
              });
              return out;
            }""")
        except Exception:
            hits = []
        print(f"  {hits if hits else '（該当なし）'}")

        # 見つかった .data エンドポイントの中身を実際に読む（売上の数字が入っている想定）
        print("\n----- .data エンドポイントの中身 -----")
        data_urls = [u for u in seen if ".data" in u]
        # 定番の候補も足す
        for cand in [f"{DIGITEL_BASE_URL}/{slug}/kpi/sales/summary.data",
                     f"{DIGITEL_BASE_URL}/{slug}/kpi/sales.data",
                     f"{DIGITEL_BASE_URL}/{slug}/kpi/sales/daily.data"]:
            if cand not in data_urls:
                data_urls.append(cand)
        for u in data_urls[:12]:
            try:
                resp = context.request.get(u, timeout=30_000)
                body = resp.body().decode("utf-8", errors="replace")
                print(f"\n  [{resp.status}] {u}\n    {body[:1400]!r}")
            except Exception as e:
                print(f"  [ERR] {u}  {type(e).__name__}: {e}")

        # ★確定URL（売上ページのCSVダウンロードリンク）でCSVを取り、中身をそのまま出す
        print("\n===== 確定URLで売上CSVを取得（無人のみ=1 / 無人+有人=0）=====")
        url = f"{DIGITEL_BASE_URL}/{slug}/kpi/sales/summary/download"
        for mujin in ("1", "0"):
            try:
                resp = context.request.get(
                    url, params={"interval": "day", "isMujin": mujin,
                                 "from": args.d_from, "to": args.d_to}, timeout=30_000)
                body = resp.body().decode("utf-8-sig", errors="replace")
                ct = resp.headers.get("content-type", "")
                print(f"\n----- isMujin={mujin}  [{resp.status}] CT={ct} -----")
                print(body[:1600])
            except Exception as e:
                print(f"  [ERR] isMujin={mujin}  {type(e).__name__}: {e}")

    print("\n===== 調査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
