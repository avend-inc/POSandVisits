"""会社Supabaseを読み取り専用で診断する（storesの現状・31店名・data.jsonのid↔店名・salesのstore_id）。

実行は GitHub Actions（Supabaseのキー/管理トークンが env にある）で。書き込みは一切しない。
  ・SQLは Supabase Management API (/v1/projects/{ref}/database/query) で SELECT のみ。
  ・data.json は 非公開バケット dashboard-data から service key で取得（最後に正常だったid↔店名）。
"""
from __future__ import annotations
import json
import os
import re

import requests

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
SVC = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""


def run_sql(sql: str):
    """Management API で SELECT を実行（読み取り専用運用）。(ok, rows|error) を返す。"""
    if not TOKEN:
        return False, "SUPABASE_ACCESS_TOKEN 未設定（Management API 使用不可）"
    try:
        r = requests.post(
            f"https://api.supabase.com/v1/projects/{REF}/database/query",
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            json={"query": sql}, timeout=60)
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}: {r.text[:300]}"
        return True, r.json()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def show(label: str, sql: str):
    print(f"\n===== {label} =====")
    ok, res = run_sql(sql)
    if not ok:
        print(f"  ✗ {res}")
        return None
    print(json.dumps(res, ensure_ascii=False, indent=2)[:6000])
    return res


def main() -> int:
    print(f"project ref = {REF}")
    # 1) storesの現在の列・主キー
    show("stores 列一覧", "select column_name,data_type,is_nullable from information_schema.columns "
         "where table_schema='public' and table_name='stores' order by ordinal_position")
    show("stores 制約(PK/UNIQUE/FK)", "select conname,contype,pg_get_constraintdef(oid) def "
         "from pg_constraint where conrelid='public.stores'::regclass")
    # 2) 31店名
    show("stores 全店名", "select name from public.stores order by name")
    # 3) sales側の store_id（＝私たちが復元すべきid集合）
    show("sales の store_id 種類", "select store_id, count(*) rows, min(business_date) f, max(business_date) t "
         "from public.sales group by store_id order by store_id")
    show("visits の store_id 種類", "select store_id, count(*) rows from public.visits group by store_id order by store_id")
    # 4) store_pos（POS接続。storesと紐付く我々の表）
    show("store_pos", "select * from public.store_pos order by store_id limit 60")
    # 5) 監査ログがあれば（前回入れていれば犯人が出る）
    show("ddl_audit(あれば)", "select * from public.ddl_audit order by at desc limit 20")
    # 6) storesを参照している外部キー（他アプリ依存）
    show("storesを参照するFK", "select conrelid::regclass::text child, pg_get_constraintdef(oid) def "
         "from pg_constraint where confrelid='public.stores'::regclass and contype='f'")

    # 7) 最後に正常だった data.json の stores（id↔name↔own↔visible↔kv）
    print("\n===== data.json の stores（非公開バケット dashboard-data） =====")
    got = False
    for path in ("dashboard-data/data.json",):
        try:
            r = requests.get(f"{URL}/storage/v1/object/{path}",
                             headers={"apikey": SVC, "Authorization": f"Bearer {SVC}"}, timeout=120)
            print(f"  GET {path} -> HTTP {r.status_code} ({len(r.content)} bytes)")
            if r.ok:
                data = r.json()
                stores = data.get("stores")
                print(f"  stores件数: {len(stores) if stores else 0}")
                print("  " + json.dumps(stores, ensure_ascii=False))
                print(f"  generated_at: {data.get('generated_at')}")
                got = True
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {e}")
    if not got:
        print("  （data.json を取得できませんでした。公開側 dashboard/data.json も試す価値あり）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
