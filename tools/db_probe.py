"""是正後の確認：cashierの store_id 分布（長野=0、山形/いわき/福井に正しく入っているか）。読み取り専用。"""
from __future__ import annotations
import json
import os
import re

import requests

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""


def run_sql(sql: str):
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": sql}, timeout=60)
    return (r.json() if r.status_code < 400 else f"HTTP {r.status_code}: {r.text[:300]}")


def main() -> int:
    print("cashier: store_id 分布（是正後）")
    print(json.dumps(run_sql("""
      select d.store_id, st.name, count(*) txn, min(d.business_date) f, max(d.business_date) t,
             sum(d.sales_ex_tax) ex_tax
      from (select distinct store_id,tx_id,business_date,sales_ex_tax
            from public.sales where pos_name='cashier') d
      left join public.stores st on st.id=d.store_id
      group by d.store_id, st.name order by ex_tax desc nulls last"""),
      ensure_ascii=False, indent=2))
    print("\n長野(50) の全明細（0のはず）:")
    print(json.dumps(run_sql("select count(*) rows from public.sales where store_id=50"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
