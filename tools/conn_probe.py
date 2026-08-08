"""レジ接続(store_pos)の状態を確認：pos_type別の件数・active状況、ezregi(SIPOS)接続の一覧。"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=90)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def show(t,sql):
    print(f"\n===== {t} =====")
    print(json.dumps(run(sql),ensure_ascii=False,indent=1)[:6000])
def main():
    show("pos_type × active 件数", """
      select pos_type, active, count(*) 件数
      from public.store_pos group by pos_type, active order by pos_type, active""")
    show("ezregi(SIPOS)接続の一覧", """
      select sp.id, sp.store_id, s.name 店舗, sp.pos_name, sp.active,
             left(coalesce(sp.url,''),42) url, (sp.pw_secret_id is not null) pw有
      from public.store_pos sp left join public.stores s on s.id=sp.store_id
      where sp.pos_type='ezregi' order by sp.active desc, sp.id""")
    show("全pos_typeの接続一覧(active含む)", """
      select pos_type, count(*) 総数, count(*) filter(where active) active数,
             count(*) filter(where not active) 停止数
      from public.store_pos group by pos_type order by pos_type""")
    return 0
raise SystemExit(main())
