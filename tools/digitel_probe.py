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

        # ダッシュボードのトップへ。
        try:
            page.goto(DIGITEL_BASE_URL + "/", wait_until="networkidle")
        except Exception:
            pass
        page.wait_for_timeout(2500)

        # 1) 「店舗を選択」ドロップダウンを開いて、店舗の一覧（名前＋スラッグ/ID）を取り出す。
        print("\n----- 店舗ドロップダウンを開いて一覧を取る -----")
        opened = False
        for sel in ["button:has-text('店舗を選択')", "text=店舗を選択",
                    "[role=combobox]", "button[aria-haspopup='listbox']"]:
            try:
                page.locator(sel).first.click(timeout=5_000)
                opened = True
                break
            except Exception:
                continue
        page.wait_for_timeout(1500)
        try:
            opts = page.evaluate(r"""() => {
              const sels = "[role=option],[cmdk-item],[data-radix-collection-item],li[role],[role=menuitem]";
              const seen=new Set(); const out=[];
              document.querySelectorAll(sels).forEach(el=>{
                const t=(el.innerText||'').trim().replace(/\s+/g,' ').slice(0,40);
                if(!t||seen.has(t))return; seen.add(t);
                const a={}; for(const at of (el.attributes||[])){ if(/value|id|slug|store|href|data-/.test(at.name)) a[at.name]=(at.value||'').slice(0,60);}
                out.push({t, a});
              });
              return out;
            }""")
        except Exception as e:
            opts = f"(取得失敗 {e})"
        print(f"  店舗オプション: {json.dumps(opts, ensure_ascii=False)}")

        # 2) Remix等がHTMLに埋め込んだデータから店舗/スラッグらしきものを探す。
        print("\n----- 埋め込みデータからスラッグ/店舗を探す -----")
        try:
            html = page.content()
        except Exception:
            html = ""
        import re as _re
        # /{slug}/kpi/visits や "slug":"..." / notime_xxx / selfurugi_xxx の断片を拾う。
        pats = [r'"[A-Za-z0-9_\-]+/kpi', r'"slug"\s*:\s*"[^"]+"',
                r'\b(?:notime|selfurugi)[_a-z0-9]+', r'"stores?"\s*:\s*\[',
                r'/[a-z0-9_\-]+/kpi/visits']
        found = []
        for p in pats:
            for m in _re.findall(p, html)[:20]:
                found.append(m)
        for m in dict.fromkeys(found)[:60]:
            print(f"  {m}")

        # 3) Remix のルート/ローダーデータ（window.__remixContext）から店舗を探す。
        try:
            rmx = page.evaluate(r"""() => {
              try{
                const c = window.__remixContext || window.__remixRouteModules || null;
                const s = JSON.stringify(c);
                if(!s) return null;
                // storeやslugを含む断片だけ返す（長すぎるので）
                const hits = (s.match(/[^{}\[\],"]*(?:slug|store|kpi|visits)[^{}\[\],"]*/gi)||[]).slice(0,40);
                return hits;
              }catch(e){ return "err:"+e; }
            }""")
        except Exception as e:
            rmx = f"(取得失敗 {e})"
        print(f"\n----- __remixContext 内の店舗/スラッグ断片 -----\n  {json.dumps(rmx, ensure_ascii=False)}")

        dump_page(page, f"digitel_dashboard_{label}")
    print("\n===== 調査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
