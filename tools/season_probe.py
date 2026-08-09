"""NOTIME全店(直営+FC)の月次季節性。1月=100%として各月の売上比を出す。
・純売上=sales_in_tax を伝票(store,pos_name,tx_id)単位で1回計上し、店舗×月で合計。
・各店の立ち上がり(オープン月+2ヶ月=最初の3ヶ月)は除外。
・進行中で不完全な当月(2026-08)は除外。
・2通り出す：①1店あたり平均(出店数の影響を除いた季節指数) ②全店合計(単純合計)。
"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
CUT=os.environ.get("SEASON_CUTOFF","2026-08-01")   # この月以降(進行中)は除外
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=120)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def main():
    base=f"""
      with nt as (select id,name from public.stores where name like 'NOTIME%'),
      tx as (
        select store_id, business_date, coalesce(pos_name,'') pn, tx_id,
               max(sales_in_tax) in_tax
        from public.sales
        where store_id in (select id from nt)
        group by store_id, business_date, coalesce(pos_name,''), tx_id
      ),
      sm as (
        select store_id, date_trunc('month',business_date::date) ym, sum(in_tax) net
        from tx group by store_id, date_trunc('month',business_date::date)
      ),
      firstm as (select store_id, min(date_trunc('month',business_date::date)) open_month from tx group by store_id),
      elig as (
        select sm.store_id, sm.ym, sm.net, extract(month from sm.ym)::int mo
        from sm join firstm f on f.store_id=sm.store_id
        where sm.ym >= f.open_month + interval '3 months'
          and sm.ym < date '{CUT}'
      )
    """
    # 対象範囲の情報
    info=run(base+"select count(distinct store_id) 店数, min(ym) 最古月, max(ym) 最新月, count(*) 店月数 from elig")
    print("対象:", json.dumps(info,ensure_ascii=False))
    # カレンダー月ごと集計
    rows=run(base+"select mo, count(*) 店月数, round(avg(net)) avg_net, round(sum(net)) sum_net from elig group by mo order by mo")
    by={r["mo"]:r for r in rows}
    janA=by.get(1,{}).get("avg_net"); janS=by.get(1,{}).get("sum_net")
    names=["","1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"]
    print(f"\n{'月':<4}{'店月数':>6}{'平均月商(税込)':>16}{'①平均比(1月=100)':>18}{'合計(税込)':>16}{'②合計比(1月=100)':>18}")
    for mo in range(1,13):
        r=by.get(mo)
        if not r:
            print(f"{names[mo]:<4}{'-':>6}{'-':>16}{'-':>18}{'-':>16}{'-':>18}"); continue
        a=r["avg_net"]; s=r["sum_net"]; n=r["店月数"]
        ia=f"{round(100*a/janA)}%" if janA else "-"
        isu=f"{round(100*s/janS)}%" if janS else "-"
        print(f"{names[mo]:<4}{n:>6}{a:>16,}{ia:>18}{s:>16,}{isu:>18}")
    # 検算用：山形 直近の店舗×月
    print("\n[検算] NOTIME山形店 月次純売上(税込):")
    v=run("""with nt as (select id from public.stores where name='NOTIME山形店'),
      tx as (select business_date, coalesce(pos_name,'') pn, tx_id, max(sales_in_tax) it
             from public.sales where store_id in (select id from nt)
             group by business_date, coalesce(pos_name,''), tx_id)
      select date_trunc('month',business_date::date)::date ym, round(sum(it)) net
      from tx group by 1 order by 1 desc limit 6""")
    print(json.dumps(v,ensure_ascii=False,indent=1))
    return 0
raise SystemExit(main())
