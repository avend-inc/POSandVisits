"""POS取り込み遅れの診断：NOTIME各店の sales 取込状況（pos_name別の日付範囲・日数）と、
経路(pos_name=Airレジ/cashier等)ごとの最新取込日を出す。対象窓は LAG_FROM/LAG_TO（既定 2026-07-25〜2026-08-07）。
"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
FROM=os.environ.get("LAG_FROM","2026-07-25"); TO=os.environ.get("LAG_TO","2026-08-07")
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=90)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def show(t,rows):
    print(f"\n===== {t} =====")
    print(json.dumps(rows,ensure_ascii=False,indent=1)[:6000])
def main():
    print(f"窓: {FROM} 〜 {TO}")
    # 1) 経路(pos_name)ごとの最新取込日と店数
    show("pos_name(経路)ごとの最新sales日・行数", run(f"""
      select coalesce(pos_name,'(空)') pos_name,
             min(business_date) min_d, max(business_date) max_d,
             count(distinct store_id) 店数, count(distinct business_date) 日数
      from public.sales
      where business_date >= '{FROM}'
      group by coalesce(pos_name,'(空)') order by max_d desc, pos_name"""))
    # 2) NOTIME各店の sales 取込日数・範囲（pos_name別）と 来店(visits)日数
    show("NOTIME各店 sales(pos_name別) と visits の取込状況", run(f"""
      with nt as (select id,name from public.stores where name like 'NOTIME%')
      select nt.name,
        coalesce(s.pos_name,'-') pos_name,
        count(distinct s.business_date) 売上日数,
        min(s.business_date) 売上最小, max(s.business_date) 売上最大,
        (select count(distinct v.business_date) from public.visits v
           where v.store_id=nt.id and v.source='digitel' and v.business_date between '{FROM}' and '{TO}') 来店日数
      from nt left join public.sales s
        on s.store_id=nt.id and s.business_date between '{FROM}' and '{TO}'
      group by nt.name, coalesce(s.pos_name,'-')
      order by nt.name, pos_name"""))
    # 3) 8/1-8/7 で取込が欠けている(売上<来店)店の、実際に売上がある日付一覧
    show("8/1-8/7 売上のある日付（遅れ店の穴を特定）", run("""
      with nt as (select id,name from public.stores where name like 'NOTIME%')
      select nt.name, s.pos_name, array_agg(distinct s.business_date order by s.business_date) 売上日
      from nt join public.sales s on s.store_id=nt.id
      where s.business_date between '2026-08-01' and '2026-08-07'
      group by nt.name, s.pos_name order by nt.name"""))
    return 0
raise SystemExit(main())
