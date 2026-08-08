"""public.stores の統合状態を確認：行数・id付き数・列・在庫列/POS列の有無・サンプル。"""
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
def show(t,sql):
    print(f"\n===== {t} =====")
    print(json.dumps(run(sql),ensure_ascii=False,indent=2)[:4000])
def main():
    show("stores 列一覧", "select column_name from information_schema.columns "
         "where table_schema='public' and table_name='stores' order by ordinal_position")
    show("件数サマリ", "select count(*) 総行数, count(id) id付き, "
         "count(*) filter(where brand is not null) brand有, "
         "count(*) filter(where nullif(area,'') is not null) area有, "
         "count(*) filter(where nullif(racks,'') is not null) racks有 from public.stores")
    show("id付き（私たちのPOS店）サンプル", "select id,name,brand,ownership,visible from public.stores where id is not null order by id limit 8")
    show("id無し（在庫のみ）行", "select name,brand,area,racks from public.stores where id is null order by name")
    show("バックアップ有無", "select table_name from information_schema.tables where table_schema='public' and table_name like 'zzz_stores_backup%'")
    return 0
raise SystemExit(main())
