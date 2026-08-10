"""共有 public.stores に、私たちのPOS 44店を「統合復元」する。

背景：avend-inventory の setup が `drop table stores cascade` で私たちの店マスタを消した。
在庫アプリの stores（PK=name / brand / area / racks / 31行）はそのまま活かし、そこへ
  ・私たちの列（id, code, digitel_slug, cashier_label, has_entry_data, ownership,
    visible, kpi_visitors, digitel_sales）を追加
  ・data.json（最後に正常＝44店・id↔name↔own↔kv↔visible）を元に、既存行は id/属性を付与、
    在庫に無い店は行を追加（brand は名前から推定、area/racks は空文字で NOT NULL 充足）
して、全アプリが1つの stores を見る形に一本化する。sales.store_id と id が全一致するか検証。

digitel_slug/cashier_label は大半が消失しているが、来店取得は店名で自己修復し slug を書き戻す
設計（run_daily）なので、id・店名・区分・表示・来店KPI対象 だけ戻せば ETL/ダッシュボードは動く。

  実行: APPLY=1 python -m tools.stores_recover   … 実際に適用（トランザクション＋検証）
        （APPLY未設定なら「ドライラン」＝生成SQLと突き合わせ結果を出すだけ・無変更）
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
APPLY = os.environ.get("APPLY") == "1"

# seed に残る4店の digitel_slug（他は消失。無くても店名で自己修復するので任意で入れる）。
SEED_SLUG = {"NOTIME山形店": "notime_yamagata", "NOTIMEいわき店": "notime_iwaki",
             "NOTIME福井店": "notime_fukui", "NOTIME下北沢店": "notime_shimokitazawa"}


def run_sql(sql: str):
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": sql}, timeout=120)
    if r.status_code >= 400:
        raise SystemExit(f"SQL失敗 HTTP {r.status_code}: {r.text[:500]}\n--- SQL ---\n{sql[:1500]}")
    return r.json()


def norm(name: str) -> str:
    """突き合わせ用の正規化：空白（半角/全角）を除去。"""
    return re.sub(r"[\s　]+", "", (name or "")).strip()


def brand_of(name: str) -> str:
    if name.startswith("NOTIME"):
        return "NOTIME"
    if name.startswith("SELFURUGI"):
        return "SELFURUGI"
    return "その他"


def q(s) -> str:
    if s is None:
        return "null"
    if isinstance(s, bool):
        return "true" if s else "false"
    if isinstance(s, (int, float)):
        return str(s)
    return "'" + str(s).replace("'", "''") + "'"


def main() -> int:
    # 1) data.json の 44店（元id↔name↔own↔kv↔visible）
    r = requests.get(f"{URL}/storage/v1/object/dashboard-data/data.json",
                     headers={"apikey": SVC, "Authorization": f"Bearer {SVC}"}, timeout=120)
    r.raise_for_status()
    ours = r.json().get("stores") or []
    print(f"data.json stores: {len(ours)}店")

    # 2) 現在の stores（在庫アプリ）行名
    cur = run_sql("select name from public.stores order by name")
    cur_names = [row["name"] for row in cur]
    cur_by_norm = {norm(n): n for n in cur_names}
    print(f"現 public.stores: {len(cur_names)}行")

    # 3) 突き合わせ
    updates, inserts = [], []
    matched_norms = set()
    for s in ours:
        nm, sid = s["name"], s["id"]
        own = s.get("own", "直営")
        kv = bool(s.get("kv", True))
        vis = bool(s.get("visible", True))
        slug = SEED_SLUG.get(nm)
        dsales = ("伊予松前" in norm(nm))
        cur_name = cur_by_norm.get(norm(nm))
        if cur_name:
            matched_norms.add(norm(nm))
            updates.append((cur_name, sid, own, vis, kv, slug, dsales))
        else:
            inserts.append((nm, brand_of(nm), sid, own, vis, kv, slug, dsales))

    inv_only = [n for n in cur_names if norm(n) not in {norm(x["name"]) for x in ours}]

    print(f"\n=== 突き合わせ結果 ===")
    print(f"  既存storesに一致（id/属性を付与）: {len(updates)}店")
    print(f"  在庫に無い→行を追加: {len(inserts)}店")
    for nm, br, sid, *_ in inserts:
        print(f"      + id={sid} {nm} (brand={br})")
    print(f"  在庫のみ（私たちは触らない・id=NULLのまま）: {len(inv_only)}行")
    for n in inv_only:
        print(f"      - {n}")

    # 4) SQL 生成
    sql = ["begin;",
           "create table if not exists public.zzz_stores_backup_20260807 as select * from public.stores;",
           """alter table public.stores
  add column if not exists id             bigint,
  add column if not exists code           text,
  add column if not exists digitel_slug   text,
  add column if not exists cashier_label  text,
  add column if not exists has_entry_data boolean default true,
  add column if not exists ownership      text default '直営',
  add column if not exists visible        boolean default true,
  add column if not exists kpi_visitors   boolean default true,
  add column if not exists digitel_sales  boolean default false;"""]
    for cur_name, sid, own, vis, kv, slug, dsales in updates:
        sets = [f"id={q(sid)}", f"ownership={q(own)}", f"visible={q(vis)}",
                f"kpi_visitors={q(kv)}", f"digitel_sales={q(dsales)}"]
        if slug:
            sets.append(f"digitel_slug=coalesce(digitel_slug,{q(slug)})")
        sql.append(f"update public.stores set {', '.join(sets)} where name={q(cur_name)};")
    for nm, br, sid, own, vis, kv, slug, dsales in inserts:
        sql.append(
            "insert into public.stores(name,brand,area,racks,id,ownership,visible,"
            "kpi_visitors,digitel_sales,digitel_slug,has_entry_data) values("
            f"{q(nm)},{q(br)},'','',{q(sid)},{q(own)},{q(vis)},{q(kv)},{q(dsales)},{q(slug)},true);")
    # id にユニーク（FKの受け皿に。NULL可＝在庫のみ行はNULLでOK）
    sql.append("create unique index if not exists stores_id_uidx on public.stores(id) where id is not null;")
    # 検証：sales.store_id が全部 stores.id に存在するか（無ければ例外でロールバック）
    sql.append("""do $$ declare n int; begin
  select count(*) into n from public.sales s left join public.stores st on st.id=s.store_id where st.id is null;
  if n>0 then raise exception '検証NG: stores.idに無い sales.store_id が % 件', n; end if;
end $$;""")
    sql.append("commit;")
    full = "\n".join(sql)

    print("\n=== 生成SQL（先頭） ===")
    print(full[:2500])
    print(f"...（全 {len(full)} 文字 / update {len(updates)} / insert {len(inserts)}）")

    if not APPLY:
        print("\n【ドライラン】APPLY=1 を付けると実際に適用します（今回は無変更）。")
        return 0

    print("\n【適用】トランザクションで実行します…")
    run_sql(full)
    print("✅ 適用完了（COMMIT）。")
    # 事後確認
    chk = run_sql("select count(*) n_stores, count(id) n_with_id from public.stores")
    orph = run_sql("select count(*) n from public.sales s left join public.stores st on st.id=s.store_id where st.id is null")
    print(f"  stores: {chk}  / 迷子sales: {orph}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
