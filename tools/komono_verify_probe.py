"""商品単価の統一（分子・分母ともレジ袋・小物を除く）を dash_daily で検証（読み取り専用）。

  セット割＋小物のある店（いわき/山形/福井）と、対照でSELFURUGI1店について、
  2026-08-01..2026-08-11 の dash_daily を集計し、
    旧 商品単価 = Σ税抜売上 ÷ Σ点数
    新 商品単価 = (Σ税抜売上 − Σレジ袋税抜 − Σ小物税抜) ÷ Σ点数   ← 統一後
    客単価(税込) = Σ税込売上 ÷ Σ取引数（レジ袋・小物込み・変更なし）
  を並べて、komono_ex が入っていること・新旧差が出ることを確認する。
"""
from __future__ import annotations
import json, os, re
import requests

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
DFROM = os.environ.get("DFROM") or "2026-08-01"
DTO = os.environ.get("DTO") or "2026-08-11"


def run(sql):
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": sql}, timeout=120)
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:600]}")
    return r.json()


def show(t, sql):
    print(f"\n===== {t} =====")
    print(json.dumps(run(sql), ensure_ascii=False, indent=1)[:6000])


def main():
    print(f"=== 商品単価 統一の検証（dash_daily）  {DFROM}..{DTO} ===")
    show("komono_ex 列の有無・非ゼロ件数", """
      select count(*) rows,
             count(komono_ex) non_null,
             count(*) filter (where coalesce(komono_ex,0)>0) positive
      from public.dash_daily""")

    show("店舗別：旧 vs 新 商品単価 / 客単価（レジ袋・小物のある店＋対照）", f"""
      select st.name,
        sum(d.sales_ex)::int ex, sum(d.bag_ex)::int bag_ex, sum(coalesce(d.komono_ex,0))::int kom_ex,
        sum(d.items)::int items, sum(d.tx)::int tx,
        round(sum(d.sales_ex)/nullif(sum(d.items),0))::int aup_old,
        round((sum(d.sales_ex)-sum(d.bag_ex)-sum(coalesce(d.komono_ex,0)))/nullif(sum(d.items),0))::int aup_new,
        round(sum(d.sales_in)/nullif(sum(d.tx),0))::int aov_incl
      from public.dash_daily d join public.stores st on st.id=d.store_id
      where d.date between '{DFROM}' and '{DTO}'
        and (st.name like '%いわき%' or st.name like '%山形%' or st.name like '%福井%'
             or st.name like '%SELFURUGI本店%')
      group by st.name order by st.name""")

    print("\n※ aup_new < aup_old なら、分子から小物・レジ袋を差し引く統一が効いている。"
          "\n  aov_incl（客単価）はレジ袋・小物込みのまま（変更していない）。")
    return 0


raise SystemExit(main())
