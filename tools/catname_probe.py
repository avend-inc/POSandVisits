"""直営で使われている『アニメ/ミュージック/ムービー/映画/Tシャツ/プリント/バンド/シャツ』系の
生カテゴリ名を店舗別に洗い出す。カテゴリ名の表記ゆれ・謎カテゴリの出所を特定する。読み取り専用。
期間 CAT_FROM/CAT_TO（既定 2026-04-01〜2026-07-31）。
"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
FROM=os.environ.get("CAT_FROM","2026-04-01"); TO=os.environ.get("CAT_TO","2026-07-31")
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=90)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def show(t,sql):
    print(f"\n===== {t} =====")
    print(json.dumps(run(sql),ensure_ascii=False,indent=1)[:6000])
def main():
    print(f"直営・期間 {FROM}〜{TO}")
    base=f"""from public.sales s
      join public.stores st on st.id=s.store_id and st.ownership='直営'
      where s.business_date between '{FROM}' and '{TO}'"""
    show("アニメ/ミュージック/ムービー/映画 を含む生カテゴリ × 店舗", f"""
      select s.line_category cat, st.name 店舗,
             round(sum(s.line_qty)) 販売数, round(sum(s.line_amount)) 金額
      {base} and (s.line_category like '%アニメ%' or s.line_category like '%ミュージック%'
                  or s.line_category like '%ムービー%' or s.line_category like '%映画%')
      group by 1,2 order by 1,2""")
    show("Tシャツ系の生カテゴリ一覧（合計）", f"""
      select s.line_category cat, count(distinct s.store_id) 店数,
             round(sum(s.line_qty)) 販売数
      {base} and s.line_category like '%シャツ%'
      group by 1 order by 販売数 desc nulls last""")
    show("『アニメ/ミュージックTシャツ』 店舗×月", f"""
      select st.name 店舗, to_char(date_trunc('month',s.business_date::date),'YYYY-MM') 月,
             round(sum(s.line_qty)) 販売数, round(sum(s.line_amount)) 金額
      {base} and s.line_category='アニメ/ミュージックTシャツ'
      group by 1,2 order by 1,2""")
    return 0
raise SystemExit(main())
