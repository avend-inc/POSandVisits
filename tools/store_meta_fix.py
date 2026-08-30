"""指定店舗の 区分(ownership)/表示名 を確認・修正する（管理APIで実行）。

読み取りは常に行い、DO_UPDATE=1 のときだけ UPDATE する（既定は dry-run）。
対象は STORE_LIKE（name ILIKE '%...%'）で1店に絞れたときだけ更新する（誤爆防止）。

env:
  STORE_LIKE     … 対象店名の一部（例: 所沢）
  SET_OWNERSHIP  … 直営 / FC（空なら区分は変えない）
  SET_NAME       … 表示名を変える場合の正式名（空なら変えない）
  DO_UPDATE      … "1" のとき実際に更新（未指定は確認のみ）
  DAYS_FROM/DAYS_TO … 日別売上の確認範囲（既定 2026-08-25〜2026-08-31）
"""
from __future__ import annotations
import json, os, re
import requests

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
STORE_LIKE = (os.environ.get("STORE_LIKE") or "所沢").strip()
SET_OWNERSHIP = (os.environ.get("SET_OWNERSHIP") or "").strip()
SET_NAME = (os.environ.get("SET_NAME") or "").strip()
DO_UPDATE = (os.environ.get("DO_UPDATE") or "").strip() == "1"
DAYS_FROM = (os.environ.get("DAYS_FROM") or "2026-08-25").strip()
DAYS_TO = (os.environ.get("DAYS_TO") or "2026-08-31").strip()
# 統合（2つに割れた店を1つに寄せる）用
CONSOLIDATE_KEEP = (os.environ.get("CONSOLIDATE_KEEP") or "").strip()
CONSOLIDATE_DROP = (os.environ.get("CONSOLIDATE_DROP") or "").strip()


def run(sql, raise_on_error=True):
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": sql}, timeout=120)
    if r.status_code >= 400:
        if raise_on_error:
            raise SystemExit(f"HTTP {r.status_code}: {r.text[:800]}")
        return {"__error__": r.text[:400], "__status__": r.status_code}
    return r.json()


def consolidate(keep: int, drop: int):
    """割れた店を1つ(keep)に寄せる。drop の売上・来店・ログを keep へ移し、
    区分/表示名を整えてから drop を削除する。削除がFKで無理なら drop を
    衝突しない死に名へ改名して無効化する。"""
    print(f"=== 統合: keep=id{keep} ← drop=id{drop} ===")
    # 事前の件数
    print("[前]", json.dumps(run(
        f"select id,name,ownership,"
        f"(select count(*) from public.sales x where x.store_id=st.id) sales,"
        f"(select count(*) from public.visits x where x.store_id=st.id) visits "
        f"from public.stores st where id in ({keep},{drop}) order by id"),
        ensure_ascii=False, default=str))
    # 1) 売上・来店を keep へ（drop側に無ければ no-op）。重複ユニーク衝突は
    #    keep が空側なので起きない前提。ingest_log は store_id を null にして退避。
    steps = [
        f"update public.sales   set store_id={keep} where store_id={drop}",
        f"update public.visits  set store_id={keep} where store_id={drop}",
        f"update public.ingest_log set store_id=null where store_id={drop}",
    ]
    if SET_OWNERSHIP or SET_NAME:
        sets = []
        if SET_OWNERSHIP:
            sets.append(f"ownership='{q(SET_OWNERSHIP)}'")
        if SET_NAME:
            sets.append(f"name='{q(SET_NAME)}'")
        steps.append(f"update public.stores set {', '.join(sets)} where id={keep}")
    for s in steps:
        print("  ->", s)
        run(s)
    # 2) drop を削除（FKで残っていれば死に名へ改名して衝突キーを外す）
    d = run(f"delete from public.stores where id={drop}", raise_on_error=False)
    if isinstance(d, dict) and d.get("__error__"):
        print(f"  ⚠️ drop削除は不可（{d['__status__']}）。死に名へ改名して無効化します: {d['__error__']}")
        run(f"update public.stores set name='旧重複_未使用_id{drop}', ownership='直営' where id={drop}")
    else:
        print(f"  drop=id{drop} を削除しました")
    print("[後]", json.dumps(run(
        f"select id,name,ownership from public.stores where id in ({keep},{drop}) order by id"),
        ensure_ascii=False, default=str))
    return 0


def q(s: str) -> str:
    return s.replace("'", "''")


def main():
    like = q(STORE_LIKE)
    print(f"=== 店舗メタ確認/修正  STORE_LIKE={STORE_LIKE!r}  DO_UPDATE={DO_UPDATE} ===")

    rows = run(f"""
        select id, code, name, ownership
        from public.stores
        where name ilike '%{like}%'
        order by id""")
    print("[該当店舗]", json.dumps(rows, ensure_ascii=False))

    # 各店舗ごとの売上件数と参照（統合の安全確認用）
    ids = [str(r["id"]) for r in rows]
    if ids:
        idlist = ",".join(ids)
        per = run(f"""
          select st.id, st.name, st.ownership,
                 (select count(*) from public.sales x where x.store_id=st.id) as sales_rows,
                 (select count(distinct (x.pos_name,x.tx_id)) from public.sales x where x.store_id=st.id) as tx,
                 (select coalesce(round(sum(x.sales_ex_tax)),0) from public.sales x where x.store_id=st.id) as ex_sum
          from public.stores st where st.id in ({idlist}) order by st.id""")
        print("[店舗ごとの売上]", json.dumps(per, ensure_ascii=False, default=str))
        for tbl, col in [("plan_data","store_id"), ("app_user_stores","store_id"),
                         ("ingest_log","store_id"), ("visits","store_id")]:
            try:
                ref = run(f"select {col} as sid, count(*) c from public.{tbl} "
                          f"where {col} in ({idlist}) group by {col} order by {col}")
                print(f"[参照 {tbl}]", json.dumps(ref, ensure_ascii=False))
            except SystemExit as e:
                print(f"[参照 {tbl}] (skip: {e})")

    # 日別の 税抜/税込（伝票単位で重複排除してから集計）
    daily = run(f"""
      with tx as (
        select distinct on (s.store_id, s.pos_name, s.tx_id)
               st.name, s.business_date, s.sales_ex_tax, s.sales_in_tax
        from public.sales s
        join public.stores st on st.id = s.store_id
        where st.name ilike '%{like}%'
          and s.business_date between '{DAYS_FROM}' and '{DAYS_TO}'
        order by s.store_id, s.pos_name, s.tx_id, s.line_no
      )
      select business_date::text as d,
             count(*) as tx,
             round(sum(sales_ex_tax))::bigint as zeinuki,
             round(sum(sales_in_tax))::bigint as zeikomi
      from tx group by business_date order by business_date""")
    print("[日別売上 税抜/税込]", json.dumps(daily, ensure_ascii=False))

    # 税抜が入っているか（null/0件数）の点検
    chk = run(f"""
      select count(*) as line_rows,
             count(*) filter (where sales_ex_tax is null) as ex_null,
             count(*) filter (where sales_ex_tax = 0)    as ex_zero,
             round(sum(sales_ex_tax))::bigint as ex_sum_lines,
             round(sum(sales_in_tax))::bigint as in_sum_lines
      from public.sales s join public.stores st on st.id=s.store_id
      where st.name ilike '%{like}%'
        and s.business_date between '{DAYS_FROM}' and '{DAYS_TO}'""")
    print("[税抜の点検(明細行ベース)]", json.dumps(chk, ensure_ascii=False))

    if not DO_UPDATE:
        print("（確認のみ。更新するには DO_UPDATE=1 を指定）")
        return 0

    # 統合モード（keep/drop 指定時）
    if CONSOLIDATE_KEEP and CONSOLIDATE_DROP:
        return consolidate(int(CONSOLIDATE_KEEP), int(CONSOLIDATE_DROP))

    if len(rows) != 1:
        print(f"⚠️ 対象が1店に絞れないため更新しません（{len(rows)}件）。STORE_LIKE を厳しくしてください。")
        return 0
    if not SET_OWNERSHIP and not SET_NAME:
        print("（SET_OWNERSHIP も SET_NAME も未指定なので更新しません）")
        return 0

    sid = rows[0]["id"]
    sets = []
    if SET_OWNERSHIP:
        if SET_OWNERSHIP not in ("直営", "FC"):
            raise SystemExit(f"SET_OWNERSHIP は 直営/FC のみ: {SET_OWNERSHIP!r}")
        sets.append(f"ownership='{q(SET_OWNERSHIP)}'")
    if SET_NAME:
        sets.append(f"name='{q(SET_NAME)}'")
    setclause = ", ".join(sets)
    upd = run(f"update public.stores set {setclause} where id={sid} returning id, name, ownership")
    print("[更新後]", json.dumps(upd, ensure_ascii=False))
    return 0


raise SystemExit(main())
