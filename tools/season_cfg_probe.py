"""⑤⑥ヒートマップの対象判定＝category_season(季節SS/AW)×grade_bands(価格帯)。
半袖シャツ/ポロシャツ/Tシャツ各種が季節設定されているか、直営の主要カテゴリのうち
どれがランク対象(=SS/AW設定あり)でどれが対象外かを実データで示す。読み取り専用。
"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
FROM=os.environ.get("CAT_FROM","2026-06-01"); TO=os.environ.get("CAT_TO","2026-07-31")
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=90)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def show(t,sql):
    print(f"\n===== {t} =====")
    print(json.dumps(run(sql),ensure_ascii=False,indent=1)[:6000])
def main():
    show("grade_bands（価格帯しきい値）", "select season,grade,lo,sort from public.grade_bands order by season,sort")
    show("category_season 全設定", "select category,season from public.category_season order by season,category")
    show("直営6-7月 カテゴリ別 販売数 × 季節設定（対象/対象外）", f"""
      select coalesce(nullif(s.line_category,''),'(空)') cat,
             coalesce(cs.season,'(未設定→対象外)') 季節,
             round(sum(s.line_qty)) 販売数
      from public.sales s
      join public.stores st on st.id=s.store_id and st.ownership='直営'
      left join public.category_season cs on cs.category=s.line_category
      where s.business_date between '{FROM}' and '{TO}'
      group by 1,2 order by 販売数 desc nulls last limit 25""")
    return 0
raise SystemExit(main())
