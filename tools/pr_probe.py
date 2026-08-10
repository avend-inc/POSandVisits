"""NOTIMEブランド×デジテール導入店の購入率を実データで検算する。
対象期間は環境変数 PR_FROM/PR_TO（既定 2026-08-01〜2026-08-07）。
・来店客数 = visits(source=digitel) の visitors 合計
・購入客数 = sales の distinct(business_date,pos_name,tx_id) 件数（=ダッシュボードの取引数）
・購入率  = 購入客数 / 来店客数
最後に「各店購入率の単純平均（=ダッシュボードのNOTIME平均）」と「合算(プール)購入率」を出す。
"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
FROM=os.environ.get("PR_FROM","2026-08-01"); TO=os.environ.get("PR_TO","2026-08-07")
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=90)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def main():
    print(f"対象期間: {FROM} 〜 {TO}\n")
    sql=f"""
    with nt as (
      select id, name, coalesce(kpi_visitors,true) kv
      from public.stores where name like 'NOTIME%'
    ),
    vis as (
      select store_id, sum(visitors)::int v, count(distinct business_date) vdays
      from public.visits
      where source='digitel' and business_date between '{FROM}' and '{TO}'
      group by store_id
    ),
    txn as (
      select store_id,
             count(distinct (business_date, coalesce(pos_name,''), tx_id)) tx,
             count(distinct business_date) tdays
      from public.sales
      where business_date between '{FROM}' and '{TO}'
      group by store_id
    )
    select nt.name, nt.kv,
      coalesce(vis.v,0) raiten, coalesce(vis.vdays,0) raiten_days,
      coalesce(txn.tx,0) kounyu, coalesce(txn.tdays,0) torihiki_days,
      case when coalesce(vis.v,0)>0 then round(100.0*coalesce(txn.tx,0)/vis.v,1) end rate
    from nt
    left join vis on vis.store_id=nt.id
    left join txn on txn.store_id=nt.id
    where vis.v is not null            -- デジテール導入店(=visitsに来店データがある)のみ
    order by rate desc nulls last;
    """
    rows=run(sql)
    print("【NOTIME × デジテール導入店（対象期間に来店データあり）】")
    print(f"{'店舗名':<22}{'KPI対象':>7}{'来店客数':>9}{'来店日数':>7}{'購入客数':>9}{'取引日数':>7}{'購入率':>8}")
    kv_rates=[]; sum_v=0; sum_tx=0
    for r in rows:
        mark="○" if r["kv"] else "×(除外)"
        rate="—" if r["rate"] is None else f"{r['rate']}%"
        print(f"{r['name']:<22}{mark:>7}{r['raiten']:>9}{r['raiten_days']:>7}{r['kounyu']:>9}{r['torihiki_days']:>7}{rate:>8}")
        if r["kv"] and r["rate"] is not None:
            kv_rates.append(float(r["rate"])); sum_v+=r["raiten"]; sum_tx+=r["kounyu"]
    print()
    if kv_rates:
        avg=sum(kv_rates)/len(kv_rates)
        pooled=100.0*sum_tx/sum_v if sum_v else None
        print(f"対象店数(KPI対象=○): {len(kv_rates)}店")
        print(f"各店購入率の単純平均  = ({' + '.join(f'{x}' for x in kv_rates)}) / {len(kv_rates)} = {round(avg,1)}%  ← ダッシュボードのNOTIME平均")
        print(f"合算(プール)購入率    = 購入客数計{sum_tx} / 来店客数計{sum_v} = {round(pooled,1)}%（参考）")
    return 0
raise SystemExit(main())
