"""category_season にカテゴリ×季節を追加/更新（upsert）。既定=アニメ/ミュージックTシャツ→SS。
環境変数 SEASON_SET="カテゴリ=季節,カテゴリ=季節" で指定可。APPLY=1 で実行、無指定はドライラン。
"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
APPLY=bool(os.environ.get("APPLY"))
SETS=os.environ.get("SEASON_SET","アニメ/ミュージックTシャツ=SS,アニメ/ムービーTシャツ=SS")
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=60)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def q(s): return s.replace("'","''")
def main():
    pairs=[]
    for tok in SETS.split(","):
        tok=tok.strip()
        if not tok or "=" not in tok: continue
        cat,se=tok.rsplit("=",1); pairs.append((cat.strip(),se.strip()))
    print(("APPLY(実行)" if APPLY else "DRY-RUN(確認のみ)")+f"  対象: {pairs}")
    # 現状
    for cat,se in pairs:
        cur=run(f"select category,season from public.category_season where category='{q(cat)}'")
        print(f"  現状 {cat!r}: {cur if cur else '(未設定)'} → 設定希望 {se}")
    if not APPLY:
        print("\n※ ドライラン。実行するには APPLY=1。")
        return 0
    for cat,se in pairs:
        run(f"""insert into public.category_season(category,season) values ('{q(cat)}','{q(se)}')
                on conflict (category) do update set season=excluded.season""")
        print(f"  ✅ upsert {cat!r} = {se}")
    # 確認
    print("\n適用後:")
    for cat,se in pairs:
        print("  ", run(f"select category,season from public.category_season where category='{q(cat)}'"))
    return 0
raise SystemExit(main())
