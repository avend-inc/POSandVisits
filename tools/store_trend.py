"""指定店舗（複数可）の月次純売上(税込)の推移。合計＋店舗別＋前月比。
対象は環境変数 TREND_STORES（カンマ区切り、既定=NOTIME熊谷店,SELFURUGI熊谷店）。
純売上=sales_in_tax を伝票(store,pos_name,tx_id)単位で1回計上。当月(進行中)は(暫定)表示。
"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
STORES=[s.strip() for s in (os.environ.get("TREND_STORES","NOTIME熊谷店,SELFURUGI熊谷店")).split(",") if s.strip()]
CUR="2026-08"  # 進行中(暫定)の月
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=120)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def num(x):
    try: return float(x)
    except (TypeError,ValueError): return 0.0
def main():
    inlist=",".join("'"+s.replace("'","''")+"'" for s in STORES)
    sql=f"""
      with s as (select id,name from public.stores where name in ({inlist})),
      tx as (
        select store_id, business_date, coalesce(pos_name,'') pn, tx_id, max(sales_in_tax) it
        from public.sales where store_id in (select id from s)
        group by store_id, business_date, coalesce(pos_name,''), tx_id
      ),
      sm as (
        select s.name nm, to_char(date_trunc('month',t.business_date::date),'YYYY-MM') ym, sum(t.it) net
        from tx t join s on s.id=t.store_id
        group by s.name, date_trunc('month',t.business_date::date)
      )
      select ym,
        {"".join([f"sum(net) filter (where nm='{s.replace(chr(39),chr(39)*2)}') s{i}," for i,s in enumerate(STORES)])}
        sum(net) total
      from sm group by ym order by ym
    """
    rows=run(sql)
    print("対象店舗:", " ＋ ".join(STORES))
    hdr = f"{'年月':<9}" + "".join(f"{s:>14}" for s in STORES) + f"{'合計':>14}{'前月比':>9}"
    print(hdr)
    prev=None
    for r in rows:
        ym=r["ym"]; tot=num(r["total"])
        cells="".join(f"{int(num(r.get('s'+str(i)))):>14,}" for i in range(len(STORES)))
        mom = f"{round((tot/prev-1)*100):+d}%" if prev else "—"
        tag = " (暫定)" if ym==CUR else ""
        print(f"{ym:<9}{cells}{int(tot):>14,}{mom:>9}{tag}")
        prev=tot
    # 年サマリ（合計）
    print("\n[年計(税込)]")
    yr={}
    for r in rows:
        y=r["ym"][:4]; yr[y]=yr.get(y,0)+num(r["total"])
    for y in sorted(yr): print(f"  {y}: {int(yr[y]):,}")
    return 0
raise SystemExit(main())
