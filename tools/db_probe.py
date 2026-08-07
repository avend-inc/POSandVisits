"""cashier売上の店舗割り当てを精査する（長野/天王台などに化けた原因＝接続のstore_id固定ピン）。

・store_pos の cashier接続一覧（各接続が store_id を固定で持つと、CSVの店舗名を無視して
  その接続の全明細が1店に寄る＝backfillの固定ピン経路）。
・cashier ソースの store_id 分布（現店名つき・全件）。
・「いわき(~4/28)」のような取り違えを、日別×store_idで可視化。
読み取り専用（SELECTのみ）。
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
    if not TOKEN:
        return False, "SUPABASE_ACCESS_TOKEN 未設定"
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


def show(label: str, sql: str, limit: int = 20000):
    print(f"\n===== {label} =====")
    ok, res = run_sql(sql)
    if not ok:
        print(f"  ✗ {res}")
        return None
    print(json.dumps(res, ensure_ascii=False, indent=2)[:limit])
    return res


def main() -> int:
    print(f"project ref = {REF}")

    # store_pos の列（どんな列があるか）
    show("store_pos 列一覧", "select column_name,data_type from information_schema.columns "
         "where table_schema='public' and table_name='store_pos' order by ordinal_position")

    # store_pos 全接続（cashier接続が store_id を固定で持っているか）
    show("store_pos 全接続", "select * from public.store_pos order by store_id nulls first")

    # cashier ソースの store_id 分布（現店名つき・全件）
    show("cashier: store_id 分布（現店名つき）", """
      select d.store_id, st.name, count(*) txn, min(d.business_date) f, max(d.business_date) t,
             sum(d.sales_ex_tax) ex_tax
      from (select distinct store_id,tx_id,business_date,sales_ex_tax
            from public.sales where pos_name='cashier') d
      left join public.stores st on st.id=d.store_id
      group by d.store_id, st.name order by d.store_id""")

    # 長野(50)・天王台(47) の日別（どの期間の伝票が来ているか）
    show("cashier: 長野(50)・天王台(47) 日別", """
      select store_id, business_date, count(*) txn, sum(sales_ex_tax) ex_tax
      from (select distinct store_id,tx_id,business_date,sales_ex_tax
            from public.sales where pos_name='cashier' and store_id in (47,50)) d
      group by store_id,business_date order by store_id,business_date""")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
