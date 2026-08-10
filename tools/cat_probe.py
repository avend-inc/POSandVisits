"""直営店のカテゴリ別 販売数・税抜売上を実データで確認する。
半袖シャツが本当に売れていないのか／別カテゴリ名(Tシャツ等)に流れていないかを見る。
期間は CAT_FROM/CAT_TO（既定 2026-06-01〜2026-07-31＝夏・完成月）。scope: ownership='直営'。
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
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=120)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def show(t,sql):
    print(f"\n===== {t} =====")
    print(json.dumps(run(sql),ensure_ascii=False,indent=1)[:6000])
def main():
    print(f"対象: 直営(ownership='直営')  期間: {FROM}〜{TO}")
    show("直営店の一覧", "select id,name from public.stores where ownership='直営' order by id")
    scope=f"""from public.sales s
      join public.stores st on st.id=s.store_id and st.ownership='直営'
      where s.business_date between '{FROM}' and '{TO}'"""
    show("カテゴリ別 販売数・税抜売上（上位25）", f"""
      select coalesce(nullif(s.line_category,''),'(空)') cat,
             round(sum(s.line_qty)) 販売数, round(sum(s.line_amount)) 金額,
             count(distinct s.store_id) 店数
      {scope}
      group by 1 order by 販売数 desc nulls last limit 25""")
    show("『シャツ/袖/T/ポロ』を含むカテゴリ全部", f"""
      select coalesce(nullif(s.line_category,''),'(空)') cat,
             round(sum(s.line_qty)) 販売数, round(sum(s.line_amount)) 金額
      {scope}
        and (s.line_category ilike '%シャツ%' or s.line_category like '%袖%'
             or s.line_category ilike '%ポロ%' or s.line_category ilike '%Ｔ%' or s.line_category like '%T%')
      group by 1 order by 販売数 desc nulls last""")
    show("半袖シャツ：店舗別・月別", f"""
      select st.name 店舗, to_char(date_trunc('month',s.business_date::date),'YYYY-MM') 月,
             round(sum(s.line_qty)) 販売数, round(sum(s.line_amount)) 金額
      from public.sales s join public.stores st on st.id=s.store_id and st.ownership='直営'
      where s.business_date between '{FROM}' and '{TO}' and s.line_category='半袖シャツ'
      group by 1,2 order by 1,2""")
    return 0
raise SystemExit(main())
