"""長野店(id=50)ほか「OPEN前なのに売上が立つ店」の中身を読み取り専用で調べる。

売上は sales（伝票明細）を tx_id で重複排除した sales_ex_tax の合計。
どのPOS接続(pos_name)経由で、いつ、いくら入っているか／その pos_name が
他店(store_id)にも紐づいていないか（＝接続の取り違え）を突き止める。
書き込みは一切しない（Management API で SELECT のみ）。
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

    # 直営として表示されている店（長野=50, 福井=3, 下北沢=4, いわき=2, 山形=1）の素性
    show("対象店の stores 行", """
      select id,name,ownership,visible,kpi_visitors,digitel_slug,digitel_sales
      from public.stores where id in (1,2,3,4,50) order by id""")

    # 長野店(50) に入っている売上：POS接続別の伝票数・税抜売上・期間
    show("長野店(50) 売上をPOS接続(pos_name)別に集計", """
      select pos_name, count(*) txn, sum(sales_ex_tax) ex_tax,
             min(business_date) f, max(business_date) t
      from (select distinct store_id,pos_name,tx_id,business_date,sales_ex_tax
            from public.sales where store_id=50) d
      group by pos_name order by ex_tax desc nulls last""")

    # 長野店(50) 日別（いつから売上が立っているか）
    show("長野店(50) 日別 税抜売上", """
      select business_date, pos_name, count(*) txn, sum(sales_ex_tax) ex_tax
      from (select distinct store_id,pos_name,tx_id,business_date,sales_ex_tax
            from public.sales where store_id=50) d
      group by business_date,pos_name order by business_date""")

    # 長野店(50) の POS接続定義（store_pos）。実体がどの店のPOSを指しているか
    show("store_pos（50と直営の接続定義）", """
      select store_id,pos_type,pos_name,url,cashier_label
      from public.store_pos where store_id in (1,2,3,4,50) order by store_id""")

    # 長野店に入っている pos_name が、他のstore_idにも紐づいていないか（取り違え検出）
    show("pos_name ごとの store_id 分布（同一接続が複数店に散っていないか）", """
      select pos_name, store_id, count(distinct tx_id) txn,
             min(business_date) f, max(business_date) t
      from public.sales
      where pos_name in (select distinct pos_name from public.sales where store_id=50)
      group by pos_name, store_id order by pos_name, store_id""")

    # 参考：全店の8月税抜売上ランキング（長野の大きさが妥当か）
    show("8月 店舗別 税抜売上（伝票dedup）上位", """
      select d.store_id, st.name, count(*) txn, sum(d.sales_ex_tax) ex_tax
      from (select distinct store_id,tx_id,business_date,sales_ex_tax
            from public.sales
            where business_date >= '2026-08-01' and business_date <= '2026-08-06') d
      left join public.stores st on st.id=d.store_id
      group by d.store_id, st.name order by ex_tax desc nulls last limit 20""")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
