"""売上推移の着地見込み検証：8月の店舗別 税抜実績・締日・経過日数・当月日数・着地見込みを出す。"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=60)
    return r.json() if r.status_code<400 else f"HTTP {r.status_code}: {r.text[:300]}"
def main():
    # 締日＝salesのある最新日
    sd=run("select max(business_date) d from public.sales")[0]["d"]
    dom=int(sd[8:10]); import calendar; dim=calendar.monthrange(int(sd[:4]),int(sd[5:7]))[1]
    print(f"締日={sd} / 経過日数(dom)={dom} / 当月日数(dim)={dim}")
    # 8月 店舗別 税抜（伝票dedup）＝ダッシュボードのdaily.ex相当
    rows=run(f"""
      select d.store_id, st.name, sum(d.sales_ex_tax) aug_ex, count(*) txn, count(distinct d.business_date) days
      from (select distinct store_id,tx_id,business_date,sales_ex_tax from public.sales
            where business_date>='{sd[:7]}-01' and business_date<='{sd}') d
      left join public.stores st on st.id=d.store_id
      where st.ownership='直営'
      group by d.store_id,st.name order by aug_ex desc""")
    print("\n店舗 / 8月実績(税抜) / 実データ日数 / 着地見込み(=実績/dom*dim)")
    for r in rows:
        ex=float(r["aug_ex"] or 0); land=ex/dom*dim if dom else 0
        print(f"  {r['name']}: 実績 {int(ex):,}円 / {r['days']}日 → 見込 {int(round(land)):,}円  （{int(ex):,}÷{dom}×{dim}）")
    return 0
raise SystemExit(main())
