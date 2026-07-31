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

        def open_dropdown() -> bool:
            for sel in ["[data-slot='select-trigger']", "[role=combobox]",
                        "button:has-text('店舗を選択')", "button[aria-haspopup='listbox']"]:
                try:
                    page.locator(sel).first.click(timeout=4_000)
                    return True
                except Exception:
                    continue
            return False

        def option_texts() -> list[str]:
            try:
                return page.evaluate(r"""() => {
                  const seen=new Set(); const out=[];
                  document.querySelectorAll("[data-slot='select-item'],[role=option],[data-radix-collection-item]").forEach(el=>{
                    const t=(el.innerText||'').trim().replace(/\s+/g,' ').slice(0,40);
                    if(t && !seen.has(t)){seen.add(t); out.push(t);}
                  });
                  return out;
                }""")
            except Exception:
                return []

        # 1) 一覧（名前）を取る。
        print("\n----- 店舗ドロップダウンの一覧 -----")
        open_dropdown()
        page.wait_for_timeout(1200)
        names = option_texts()
        print(f"  店舗名: {json.dumps(names, ensure_ascii=False)}")

        # 2) 各店を選択して、URL / ダウンロードリンク から スラッグ を確定する。
        print("\n----- 各店の スラッグ（URL/ダウンロードリンク）-----")
        mapping = {}
        for name in [n for n in names if n and n != "本部"]:
            if not open_dropdown():
                print(f"  {name}: ドロップダウンを開けませんでした"); continue
            page.wait_for_timeout(500)
            clicked = False
            for sel in [f"[data-slot='select-item']:has-text('{name}')",
                        f"[role=option]:has-text('{name}')"]:
                try:
                    page.locator(sel).first.click(timeout=3_000); clicked = True; break
                except Exception:
                    continue
            if not clicked:
                print(f"  {name}: 選択できませんでした"); continue
            page.wait_for_timeout(1200)
            info = page.evaluate(r"""() => {
              const dl=[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))
                       .find(h=>h && h.includes('/kpi/visits'));
              return {path: location.pathname, dl: dl||null};
            }""")
            mapping[name] = info
            print(f"  {name} -> path={info.get('path')} dl={info.get('dl')}")
        print(f"\n  === 店舗→スラッグ候補 ===\n  {json.dumps(mapping, ensure_ascii=False)}")

        dump_page(page, f"digitel_dashboard_{label}")
    print("\n===== 調査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
