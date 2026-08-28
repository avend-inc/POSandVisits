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

        # 0) 実際の取り込みと同じロジック（discover_stores）で「拾えた店／取りこぼし
        #    候補」を出す。ある店だけ来店客数が入らない不具合の原因究明・横展開用。
        print("\n----- 取り込みロジックの判定（discover_stores）-----")
        try:
            raw_html = page.content().replace('\\"', '"')
            kept, dropped = digitel_fetch.scan_store_pairs(raw_html)
            print(f"  ✅ 拾えた店 {len(kept)}件:")
            for nm, sl in sorted(kept.items()):
                print(f"       {nm!r} → {sl!r}")
            if dropped:
                print(f"  ⚠️ 取りこぼし候補 {len(dropped)}件（来店データが入らない可能性）:")
                for nm, sl in dropped:
                    print(f"       {nm!r} → {sl!r}")
            else:
                print("  取りこぼし候補: なし")
        except Exception as e:
            print(f"  （判定に失敗: {e}）")

        # 0b) 気になる店名キーワードが生HTMLのどこにどう入っているかを直接探す。
        #     「徳島沖浜が来店に入らない」→ 名前の書かれ方／スラッグの形を目で確認する。
        keywords = os.environ.get("PROBE_KEYWORDS", "徳島,沖浜,GARAGE").split(",")
        print(f"\n----- キーワード探索（生HTML内）: {keywords} -----")
        try:
            hay = page.content()
        except Exception:
            hay = ""
        for kw in [k.strip() for k in keywords if k.strip()]:
            idx = hay.find(kw)
            if idx < 0:
                print(f"  「{kw}」: HTMLに見当たりません（この画面には出ていない）")
                continue
            seg = hay[max(0, idx - 120):idx + 120].replace("\n", " ")
            print(f"  「{kw}」: …{seg}…")

        # 1) 一覧（名前）を取る。
        print("\n----- 店舗ドロップダウンの一覧 -----")
        open_dropdown()
        page.wait_for_timeout(1200)
        names = option_texts()
        print(f"  店舗名: {json.dumps(names, ensure_ascii=False)}")

        # 2) 店舗リストは Remix のHTMLに埋め込まれている想定。各店名の周辺を出して
        #    スラッグ/IDの入り方を確認する（単発・堅牢）。
        print("\n----- HTML内 店舗名の周辺（スラッグ/IDの確認）-----")
        try:
            html = page.content()
        except Exception:
            html = ""
        print(f"  （HTML長: {len(html)}）")
        for name in [n for n in names if n and n != "本部"][:4]:
            idx = html.find(name)
            if idx < 0:
                print(f"  {name}: HTMLに見つからず"); continue
            ctx = html[max(0, idx - 160):idx + 60].replace("\n", " ")
            print(f"  【{name}】…{ctx}…")

        # 3) 埋め込みJSON配列から {name, slug/id} の組を機械的に拾ってみる。
        print("\n----- 埋め込みデータから 名前↔スラッグ を抽出 -----")
        try:
            pairs = page.evaluate(r"""(names) => {
              const html = document.documentElement.outerHTML;
              const out = {};
              for(const nm of names){
                if(nm==='本部')continue;
                // 名前の近く（前後300字）から slug/uuid/id らしき文字を拾う
                let i = html.indexOf(nm);
                if(i<0){ out[nm]=null; continue; }
                const seg = html.slice(Math.max(0,i-300), i+300);
                const m = seg.match(/"(?:slug|storeSlug|store_slug|code|uuid|id)"\s*:\s*"([^"]{2,60})"/g);
                out[nm] = m ? m.slice(0,6) : null;
              }
              return out;
            }""", names)
        except Exception as e:
            pairs = f"(失敗 {e})"
        print(f"  {json.dumps(pairs, ensure_ascii=False)}")


        # 4) LINE配信のデータがどこにあるかを探す。
        #    デジテールストア（＝ネブラスカ製）は来店・売上と同じ画面にLINE配信も
        #    持っている。来店CSVが /{slug}/kpi/visits/download にあるので、
        #    LINEも店舗配下のどこかにあるはず。その場所をここで突き止める。
        #    トップは店舗選択だけのSPAでリンクが出ないため、店舗ページへ入って調べる。
        # 画面の保存は先に済ませる（この出力が長いので、調査結果を最後に出すため）
        dump_page(page, f"digitel_dashboard_{label}")

        print("\n\n" + "=" * 60)
        print("===== LINE配信データの在りか（ここが結論）=====")
        print("=" * 60)
        try:
            kept2, _ = digitel_fetch.scan_store_pairs(page.content().replace('\\"', '"'))
        except Exception:
            kept2 = {}
        slug = sorted(kept2.values())[0] if kept2 else None
        if not slug:
            print("  店舗スラッグを取れませんでした")
            return 0

        print(f"  店舗: {slug}\n")
        page.goto(f"{DIGITEL_BASE_URL}/{slug}", wait_until="networkidle", timeout=25_000)
        page.wait_for_timeout(1500)

        # サイドバーの見出し（KPIデータ・メッセージ配信・LINE公式アカウント…）は
        # 押すと中のリンクが開く折りたたみ。全部開いてからリンクを集める。
        try:
            n = page.locator("button.peer\\/menu-button").count()
        except Exception:
            n = 0
        opened = 0
        for i in range(n):
            try:
                page.locator("button.peer\\/menu-button").nth(i).click(timeout=2_500)
                page.wait_for_timeout(350)
                opened += 1
            except Exception:
                continue
        print(f"  サイドバーの見出しを {opened}/{n} 個ひらきました\n")

        try:
            links = page.evaluate(r"""() => {
              const seen=new Set(), out=[];
              document.querySelectorAll("a[href]").forEach(a=>{
                const h=a.getAttribute("href")||"";
                const t=(a.innerText||"").trim().replace(/\s+/g,' ').slice(0,26);
                const k=t+"|"+h;
                if(!h||h.startsWith("#")||seen.has(k))return;
                seen.add(k); out.push({t,h});
              });
              return out;
            }""")
        except Exception as e:
            links = []
            print(f"  リンクを取れません: {e}")
        print(f"  ---- 画面内のリンク {len(links)}件 ----")
        for x in links:
            star = "★" if any(k in x["t"] for k in
                              ("配信", "LINE", "会員", "友だち", "友達", "メッセージ", "顧客")) else " "
            print(f"   {star} {x['t'] or '(文字なし)':<26} {x['h']}")

        # 配信履歴の画面を開いて、CSVの出口（リンク/ボタン/通信）を探す。
        #   会員関連（友だち数）は /kpi/members/friends/download で取れると確認済み。
        #   残るは配信実績なので、そのページの中身を見る。
        print("\n  ---- 配信履歴のページを調べる ----")
        msg_req: list[str] = []
        page.on("request", lambda r: msg_req.append(r.url))
        for path in (f"/{slug}/messages/history", f"/{slug}/products/coupons/list",
                     f"/{slug}/campaigns/list"):
            url = DIGITEL_BASE_URL + path
            try:
                resp = page.goto(url, wait_until="networkidle", timeout=25_000)
                page.wait_for_timeout(1800)
                info = page.evaluate(r"""() => ({
                  links: Array.from(document.querySelectorAll("a[href]"))
                    .map(a=>({t:(a.innerText||'').trim().replace(/\s+/g,' ').slice(0,22),h:a.getAttribute('href')}))
                    .filter(x=>x.h&&!x.h.startsWith('#')),
                  btns: Array.from(document.querySelectorAll("button"))
                    .map(b=>(b.innerText||'').trim().replace(/\s+/g,' ')).filter(Boolean),
                  ths: Array.from(document.querySelectorAll("th,thead td"))
                    .map(t=>(t.innerText||'').trim()).filter(Boolean),
                  body:(document.body.innerText||'').replace(/\s+/g,' ').slice(0,500)})""")
                print(f"\n   {url} → HTTP {resp.status if resp else '?'}")
                print(f"     表の見出し: {info['ths']}")
                print(f"     ボタン: {', '.join(dict.fromkeys(info['btns']))[:300]}")
                for x in info["links"]:
                    if any(k in (x["t"] + x["h"]).lower() for k in ("download", "csv", "ダウンロード")):
                        print(f"     ★ CSV候補: {x['t']} → {x['h']}")
                print(f"     画面: {info['body'][:400]}")
            except Exception as e:
                print(f"   {url} → 開けません {str(e)[:70]}")

        # 配信履歴の .data（Remixのローダー）も見る。CSVが無ければこれで取れる
        print("\n  ---- 配信履歴のデータを直接取ってみる ----")
        for path in (f"/{slug}/messages/history/download?from=2026-08-01&to=2026-08-27",
                     f"/{slug}/messages/history.data",
                     f"/{slug}/messages/history/download"):
            url = DIGITEL_BASE_URL + path
            try:
                r = context.request.get(url, timeout=60_000)
                head = r.body()[:400].decode("utf-8", errors="replace").replace("\n", " / ")
                mark = "★" if r.status == 200 else " "
                print(f"   {mark} HTTP {r.status}  {path}")
                if r.status == 200:
                    print(f"        先頭: {head[:320]}")
            except Exception as e:
                print(f"     失敗 {path} → {str(e)[:60]}")

        hits = [u for u in dict.fromkeys(msg_req)
                if any(k in u.lower() for k in ("download", "csv", ".data", "message"))]
        if hits:
            print("\n  ★ 配信履歴まわりの通信:")
            for u in hits[:25]:
                print(f"       {u}")

    print("\n===== 調査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
