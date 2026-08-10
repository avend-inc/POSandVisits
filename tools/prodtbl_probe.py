"""商品(product/item/sku/goods)系テーブルの有無とPK・主要列を確認する。読み取り専用。"""
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
    show("public 全テーブルとPK列", """
      select t.table_name,
             coalesce(string_agg(kcu.column_name, ',' order by kcu.ordinal_position),'(PKなし)') pk
      from information_schema.tables t
      left join information_schema.table_constraints tc
        on tc.table_schema=t.table_schema and tc.table_name=t.table_name and tc.constraint_type='PRIMARY KEY'
      left join information_schema.key_column_usage kcu
        on kcu.constraint_name=tc.constraint_name and kcu.table_schema=t.table_schema
      where t.table_schema='public' and t.table_type='BASE TABLE'
      group by t.table_name order by t.table_name""")
    show("商品系テーブル候補の列一覧", """
      select table_name, column_name, data_type
      from information_schema.columns
      where table_schema='public'
        and (table_name ilike '%product%' or table_name ilike '%item%' or table_name ilike '%sku%'
             or table_name ilike '%goods%' or table_name ilike '%商品%' or table_name ilike '%catalog%')
      order by table_name, ordinal_position""")
    show("sales の商品識別に使えそうな列（インハウスコード等の有無）", """
      select column_name, data_type from information_schema.columns
      where table_schema='public' and table_name='sales'
        and (column_name ilike '%code%' or column_name ilike '%sku%' or column_name ilike '%jan%'
             or column_name ilike '%item%' or column_name ilike '%product%' or column_name ilike '%barcode%'
             or column_name ilike '%inhouse%' or column_name ilike '%hinban%' or column_name ilike '%品番%')
      order by column_name""")
    return 0
raise SystemExit(main())
