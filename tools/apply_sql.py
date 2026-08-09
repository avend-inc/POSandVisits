"""指定した .sql ファイルを Supabase Management API で実行する（管理者運用・DDL/DML可）。
   SQL_FILES="sql/019_...,sql/020_..." で対象を指定（カンマ区切り）。
   Management API は superuser 実行＝RLSを素通りするので、ポリシー作成やRPC定義に使える。
"""
from __future__ import annotations
import os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
FILES=[f.strip() for f in (os.environ.get("SQL_FILES") or "").split(",") if f.strip()]
def run(sql):
    return requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},
        json={"query":sql}, timeout=180)
def main():
    if not FILES:
        print("SQL_FILES 未指定"); return 1
    for f in FILES:
        sql=open(f, encoding="utf-8").read()
        print(f"\n===== 適用: {f}（{len(sql)}文字）=====")
        r=run(sql)
        print("HTTP", r.status_code, (r.text or "")[:600])
        if r.status_code>=400:
            print("❌ 失敗（以降は中断）"); return 1
        print("✅ OK")
    print("\n===== 確認：テーブル =====")
    print(run("select table_name from information_schema.tables where table_schema='public' and table_name='app_user_stores'").text[:300])
    print("\n===== 確認：関数 =====")
    print(run("select proname from pg_proc where proname in ('is_hq','can_view_store','franchise_add_member','franchise_remove_member','franchise_list_members') order by proname").text[:400])
    print("\n===== 確認：storage RLSポリシー =====")
    print(run("select policyname from pg_policies where schemaname='storage' and tablename='objects' and policyname like 'dashboard-data%' order by policyname").text[:400])
    return 0
raise SystemExit(main())
