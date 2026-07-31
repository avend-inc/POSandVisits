"""
デジテール「売上明細データ（商品別明細）」ダウンロードURL 調査プローブ

目的:
  会計データ → 売上データダウンロード の画面で「売上明細データ（商品別明細）」を
  選んで［ダウンロード］したときに実際に叩かれる URL・メソッド・パラメータを
  突き止める（sales_details_*.csv を取得する本番実装のため）。取り込みはしない。

出すもの（Actionsログ）:
  ・ログイン後、売上データダウンロード画面までの遷移で観測した通信URL
  ・画面内のリンク/ボタン/チェックボックス（ラベル）一覧
  ・［ダウンロード］押下で発生した download の URL・ファイル名・CSV先頭数行
  ・download が取れないときは、観測した download/export/accounting 系URLを列挙

使い方（GitHub Actions「デジテール売上CSVの調査」から。mode=detail で本プローブ）:
  python -m tools.digitel_detail_probe --slug notime_masakicho
  python -m tools.digitel_detail_probe --sf --slug selffurugi_utsunomiya
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
from etl.browser import browser_page  # noqa: E402
from etl.settings import DIGITEL_BASE_URL, load_dotenv  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sf", action="store_true", help="SELFURUGIアカウントで調べる")
    ap.add_argument("--slug", default="notime_masakicho", help="調べる店舗スラッグ")
    ap.add_argument("--from", dest="d_from", default="2026-07-31")
    ap.add_argument("--to", dest="d_to", default="2026-07-31")
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
    print(f"===== デジテール 売上明細ダウンロード 調査（{label} / slug={slug}）=====")
    print(f"  期間: {args.d_from} 〜 {args.d_to}")

    seen: list[tuple[str, str]] = []
    with browser_page(headless=True) as (page, context):
        page.on("request", lambda req: seen.append((req.method, req.url)))
        digitel_fetch.login(page, user, pw)

        # 1) 売上データダウンロード画面へ。まずは想定URLを順に試し、だめならメニューから辿る。
        candidates = [
            f"/{slug}/accounting/sales-download",
            f"/{slug}/accounting/download",
            f"/{slug}/accounting/sales/download",
            f"/{slug}/kpi/accounting/download",
            f"/{slug}/sales/download",
        ]
        opened = False
        for path in candidates:
            try:
                resp = page.goto(DIGITEL_BASE_URL + path, wait_until="networkidle")
                page.wait_for_timeout(1500)
                body = page.content()
                if ("売上データダウンロード" in body) or ("商品別明細" in body):
                    print(f"\n★ ダウンロード画面を発見: {path}  (HTTP {resp.status if resp else '?'})")
                    opened = True
                    break
            except Exception as e:
                print(f"  goto {path} 失敗: {type(e).__name__}: {e}")

        if not opened:
            # メニューのリンク文言から辿る
            print("\n直URLでは見つからず。メニュー『売上データダウンロード』を探してクリックします。")
            try:
                page.goto(DIGITEL_BASE_URL + f"/{slug}", wait_until="networkidle")
                page.wait_for_timeout(1500)
                link = page.get_by_text("売上データダウンロード", exact=False).first
                link.click(timeout=8000)
                page.wait_for_timeout(2500)
                opened = "商品別明細" in page.content()
                print(f"  クリック後URL: {page.url}  発見={opened}")
            except Exception as e:
                print(f"  メニュー探索失敗: {type(e).__name__}: {e}")

        # 2) 画面内の入力・ボタン・チェックボックス（ラベル）を一覧
        print("\n----- 画面内のフォーム部品（ラベル/種別） -----")
        try:
            parts = page.evaluate(r"""() => {
              const out=[];
              document.querySelectorAll('input,button,a[href],label,select').forEach(el=>{
                const lbl=(el.innerText||el.getAttribute('aria-label')||el.getAttribute('placeholder')||'').trim().slice(0,40);
                const t=el.tagName.toLowerCase();
                const type=el.getAttribute('type')||'';
                const href=el.getAttribute('href')||'';
                if(lbl||href) out.push({t,type,lbl,href});
              });
              return out.slice(0,80);
            }""")
            for p in parts:
                print(f"  {p['t']}/{p.get('type','')}  {p['lbl']}  {p.get('href','')}")
        except Exception as e:
            print(f"  取得失敗: {type(e).__name__}: {e}")

        # 3) 「売上明細データ（商品別明細）」だけにチェック → ダウンロード押下でDL捕捉
        print("\n----- ［ダウンロード］押下で download を捕捉 -----")
        try:
            # 商品別明細のチェックを付ける（ラベル文言で探す）
            try:
                cb = page.get_by_label("売上明細データ（商品別明細）", exact=False)
                cb.check(timeout=4000)
                print("  『売上明細データ（商品別明細）』にチェックしました")
            except Exception as e:
                print(f"  チェック操作は省略/失敗（既定のまま試す）: {type(e).__name__}: {e}")

            with page.expect_download(timeout=15000) as dl_info:
                page.get_by_role("button", name="ダウンロード").click(timeout=8000)
            dl = dl_info.value
            print(f"  ✅ download URL   : {dl.url}")
            print(f"  ✅ suggested name : {dl.suggested_filename}")
            save = ROOT / "debug" / dl.suggested_filename
            save.parent.mkdir(exist_ok=True)
            dl.save_as(str(save))
            head = save.read_bytes()[:1200].decode("utf-8-sig", errors="replace")
            print(f"  先頭:\n{head}")
        except Exception as e:
            print(f"  download 捕捉できず: {type(e).__name__}: {e}")

        # 4) 観測した download/export/accounting/sales 系のURLを列挙（当たり付け）
        print("\n----- 観測URL（download/export/accounting/sales/detail 含む） -----")
        keys = ("download", "export", "accounting", "detail", "sales")
        uniq = []
        for m, u in seen:
            if any(k in u.lower() for k in keys) and (m, u) not in uniq:
                uniq.append((m, u))
        for m, u in uniq[:60]:
            print(f"  {m:4} {u}")

    print("\n===== 調査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
