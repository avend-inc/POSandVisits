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
# 二重計上の掃除（pos_name 食い違い）用
DEDUP_STORE_ID = (os.environ.get("DEDUP_STORE_ID") or "").strip()
KEEP_POS_NAME = (os.environ.get("KEEP_POS_NAME") or "cashier").strip()


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

    # 事業計画(bizplan)の中身を直接確認（目標設定モーダルが月ごとに拾う元データ）
    if (os.environ.get("BIZPLAN_PROBE") or "").strip() == "1":
        print("\n=== 事業計画(bizplan_monthly) 直接確認 ===")
        print("[item別 件数]", json.dumps(run(
            "select item, count(*) n from public.bizplan_monthly group by item order by n desc"),
            ensure_ascii=False, default=str))
        print("[売上 item の ym別 件数]", json.dumps(run(
            "select ym, count(*) n, count(distinct plan_key) plans "
            "from public.bizplan_monthly where item='売上' group by ym order by ym"),
            ensure_ascii=False, default=str))
        print("[store計画の pos_store_name 一覧(先頭20)]", json.dumps(run(
            "select pos_store_name, plan_key from public.bizplan_plans "
            "where kind='store' and pos_store_name is not null order by pos_store_name limit 20"),
            ensure_ascii=False, default=str))
        print("[いわきの 売上計画 月別]", json.dumps(run(
            "select p.pos_store_name, m.ym, m.amount from public.bizplan_monthly m "
            "join public.bizplan_plans p on p.plan_key=m.plan_key "
            "where p.kind='store' and m.item='売上' and p.pos_store_name ilike '%いわき%' "
            "order by m.ym"), ensure_ascii=False, default=str))
        return 0

    # 予測: 8月の商品単価(aup)から、9月の売上着地に対する商品販売数を出す。
    #   aup = (税抜売上 − レジ袋 − 小物 − 明細なし) ÷ 商品販売数(小物/レジ袋/クーポン除外)
    #   9月商品単価 ≈ 8月aup × 1.10、9月販売数 = 9月売上 ÷ 9月商品単価。
    if (os.environ.get("AUG_AUP") or "").strip() == "1":
        # 対象店（表示名の一部）と 9月売上着地見込み(税抜, 円)
        targets = [
            ("下北沢", 3_000_000),
            ("山形",   1_750_000),
            ("いわき", 1_750_000),
            ("福井",   3_000_000),
            ("長野",   4_000_000),
        ]
        AUG_FROM, AUG_TO = "2026-08-01", "2026-09-01"
        print(f"\n=== 9月 商品販売数の予測（8月 商品単価×1.10 ベース） ===")
        print(f"  8月実績期間: {AUG_FROM} 〜 {AUG_TO}未満")
        print(f"  {'店舗':<8}{'8月税抜':>12}{'8月販売数':>10}{'8月商品単価':>12}"
              f"{'9月単価110%':>12}{'9月売上見込':>12}{'9月販売数(予測)':>14}")
        for nm, sep_sales in targets:
            lk = q(nm)
            res = run(f"""
              with base as (
                select s.store_id, s.pos_name, s.tx_id, s.line_no, s.sales_ex_tax,
                       coalesce(nullif(trim(s.line_category),''),'その他') as cat,
                       s.line_qty, s.line_amount
                from public.sales s join public.stores st on st.id=s.store_id
                where st.name ilike '%{lk}%'
                  and s.business_date >= '{AUG_FROM}' and s.business_date < '{AUG_TO}'
              ),
              tx as (
                select distinct on (store_id,pos_name,tx_id) sales_ex_tax
                from base order by store_id,pos_name,tx_id,line_no
              )
              select
                (select coalesce(round(sum(sales_ex_tax)),0) from tx)::bigint as ex,
                coalesce(round(sum(line_amount) filter (where cat='レジ袋')),0)::bigint as bag_ex,
                coalesce(round(sum(line_amount) filter (where cat='小物')),0)::bigint as kom_ex,
                coalesce(round(sum(line_amount) filter (where cat in ('（明細なし）','明細なし'))),0)::bigint as nocat_ex,
                coalesce(sum(line_qty) filter (where cat not in ('レジ袋','クーポン','小物')),0)::bigint as it
              from base""")
            # 管理APIは行の配列を返す
            r0 = res[0] if isinstance(res, list) and res else {}
            ex = float(r0.get("ex", 0) or 0)
            bag = float(r0.get("bag_ex", 0) or 0)
            kom = float(r0.get("kom_ex", 0) or 0)
            noc = float(r0.get("nocat_ex", 0) or 0)
            it = float(r0.get("it", 0) or 0)
            aup = (ex - bag - kom - noc) / it if it > 0 else 0.0
            sep_aup = aup * 1.10
            sep_units = (sep_sales / sep_aup) if sep_aup > 0 else 0.0
            print(f"  {nm:<8}{ex:>12,.0f}{it:>10,.0f}{aup:>12,.0f}"
                  f"{sep_aup:>12,.0f}{sep_sales:>12,.0f}{sep_units:>14,.0f}")
        return 0

    # 横断カバレッジ: ある営業日に売上がある店を全店一覧＋直近日の店数推移
    cov = (os.environ.get("COVERAGE_DATE") or "").strip()
    if cov:
        print(f"\n=== 横断カバレッジ: business_date={cov} に売上がある店（全店） ===")
        print("[当日 店舗別]", json.dumps(run(f"""
          with tx as (select distinct on (s.store_id,s.pos_name,s.tx_id)
                 s.store_id, s.sales_ex_tax
               from public.sales s where s.business_date='{cov}'
               order by s.store_id,s.pos_name,s.tx_id,s.line_no)
          select st.name, count(*) tx, round(sum(tx.sales_ex_tax))::bigint ex
          from tx join public.stores st on st.id=tx.store_id
          group by st.name order by st.name"""), ensure_ascii=False, default=str))
        print("[直近5営業日: 売上のある店数・総伝票]", json.dumps(run("""
          with tx as (select distinct on (s.store_id,s.pos_name,s.tx_id)
                 s.business_date, s.store_id
               from public.sales s
               where s.business_date >= (date '""" + cov + """' - 4)
                 and s.business_date <= '""" + cov + """'
               order by s.store_id,s.pos_name,s.tx_id,s.line_no)
          select business_date::text d, count(distinct store_id) stores, count(*) tx
          from tx group by business_date order by business_date"""),
            ensure_ascii=False, default=str))
        print(f"[所沢の直近5日]", json.dumps(run(f"""
          with tx as (select distinct on (s.store_id,s.pos_name,s.tx_id)
                 s.business_date, s.sales_ex_tax
               from public.sales s join public.stores st on st.id=s.store_id
               where st.name ilike '%所沢%'
                 and s.business_date >= (date '{cov}' - 4) and s.business_date <= '{cov}'
               order by s.store_id,s.pos_name,s.tx_id,s.line_no)
          select business_date::text d, count(*) tx, round(sum(sales_ex_tax))::bigint ex
          from tx group by business_date order by business_date"""),
            ensure_ascii=False, default=str))
        return 0

    # 診断: 指定営業日(締日)の売上が、実際は複数日の処理を束ねていないかを見る
    diag_date = (os.environ.get("DIAG_DATE") or "").strip()
    if diag_date:
        print(f"\n=== 診断: {STORE_LIKE} business_date(締日)={diag_date} の内訳 ===")
        print("[レジ別・伝票/税抜]", json.dumps(run(f"""
          with tx as (
            select distinct on (s.store_id,s.pos_name,s.tx_id)
                   s.pos_name, s.tx_id, s.sales_ex_tax, s.ts
            from public.sales s join public.stores st on st.id=s.store_id
            where st.name ilike '%{like}%' and s.business_date='{diag_date}'
            order by s.store_id,s.pos_name,s.tx_id,s.line_no)
          select pos_name, count(*) tx, round(sum(sales_ex_tax))::bigint ex
          from tx group by pos_name order by ex desc"""), ensure_ascii=False, default=str))
        print("[処理日(ts)別の伝票数・税抜]", json.dumps(run(f"""
          with tx as (
            select distinct on (s.store_id,s.pos_name,s.tx_id)
                   (s.ts at time zone 'Asia/Tokyo')::date as tsd, s.sales_ex_tax
            from public.sales s join public.stores st on st.id=s.store_id
            where st.name ilike '%{like}%' and s.business_date='{diag_date}'
            order by s.store_id,s.pos_name,s.tx_id,s.line_no)
          select tsd::text d, count(*) tx, round(sum(sales_ex_tax))::bigint ex
          from tx group by tsd order by tsd"""), ensure_ascii=False, default=str))
        print("[高額伝票 上位8]", json.dumps(run(f"""
          with tx as (
            select distinct on (s.store_id,s.pos_name,s.tx_id)
                   s.tx_id, s.sales_ex_tax, s.sales_in_tax, s.ts::text ts
            from public.sales s join public.stores st on st.id=s.store_id
            where st.name ilike '%{like}%' and s.business_date='{diag_date}'
            order by s.store_id,s.pos_name,s.tx_id,s.line_no)
          select tx_id, round(sales_ex_tax)::bigint ex, round(sales_in_tax)::bigint zeikomi, ts
          from tx order by sales_ex_tax desc limit 8"""), ensure_ascii=False, default=str))
        return 0

    # 診断: pos_name の食い違いによる二重計上を全店で洗い出す
    if (os.environ.get("DIAG_DUP") or "").strip() == "1":
        print("\n=== 診断: cashier接続の pos_name と 二重計上スキャン ===")
        print("[store_pos(cashier)]", json.dumps(run(
            "select id, store_id, coalesce(pos_name,'') pos_name, active "
            "from public.store_pos where pos_type='cashier' order by id"),
            ensure_ascii=False, default=str))
        print(f"[{STORE_LIKE}: レジ名×締日 伝票数]", json.dumps(run(f"""
          with tx as (select distinct on (s.store_id,s.pos_name,s.tx_id)
                 s.pos_name, s.business_date, s.sales_ex_tax
               from public.sales s join public.stores st on st.id=s.store_id
               where st.name ilike '%{like}%' and s.business_date>='2026-08-01'
               order by s.store_id,s.pos_name,s.tx_id,s.line_no)
          select business_date::text d, pos_name, count(*) tx,
                 round(sum(sales_ex_tax))::bigint ex
          from tx group by business_date,pos_name order by d,pos_name"""),
            ensure_ascii=False, default=str))
        print("[全店 二重計上スキャン(8月・同一レシートが複数pos_name)]", json.dumps(run("""
          select st.name, count(*) dup_receipts,
                 string_agg(distinct d.np::text, ',') pos_name_counts
          from (
            select s.store_id, split_part(s.tx_id,':',2) rcpt,
                   count(distinct s.pos_name) np
            from public.sales s
            where s.business_date >= '2026-08-01'
            group by s.store_id, split_part(s.tx_id,':',2)
            having count(distinct s.pos_name) > 1
          ) d join public.stores st on st.id=d.store_id
          group by st.name order by dup_receipts desc"""),
            ensure_ascii=False, default=str))
        return 0

    # 二重計上の掃除: 同一レシートが keep_pos_name と他レジ名で二重に入っている店で、
    # 「他レジ名かつ keep 側にも同じレシートがある行」だけを消す（keep単独の行は残す）。
    if DEDUP_STORE_ID:
        sid = int(DEDUP_STORE_ID); keep = q(KEEP_POS_NAME)
        print(f"\n=== 二重計上の掃除: store_id={sid} / 残す pos_name='{KEEP_POS_NAME}' ===")
        print("[前: レジ名別の行数]", json.dumps(run(
            f"select pos_name, count(*) rows from public.sales where store_id={sid} "
            f"group by pos_name order by pos_name"), ensure_ascii=False, default=str))
        cond = (f"store_id={sid} and pos_name<>'{keep}' and split_part(tx_id,':',2) in "
                f"(select split_part(tx_id,':',2) from public.sales where store_id={sid} and pos_name='{keep}')")
        cnt = run(f"select count(*) c from public.sales where {cond}")
        print("[削除対象の重複行数]", json.dumps(cnt, ensure_ascii=False, default=str))
        if not DO_UPDATE:
            print("（確認のみ。実削除するには DO_UPDATE=1 を指定）")
            return 0
        run(f"delete from public.sales where {cond}")
        print("[後: レジ名別の行数]", json.dumps(run(
            f"select pos_name, count(*) rows from public.sales where store_id={sid} "
            f"group by pos_name order by pos_name"), ensure_ascii=False, default=str))
        # 再発防止: cashier接続の表示名(pos_name)が cashier/空 以外だと、dailyがその名前で
        # 書き込み backfill('cashier')と食い違って二重化する。custom名を空にして 'cashier' に寄せる。
        fixconn = run("update public.store_pos set pos_name='' "
                      "where pos_type='cashier' and coalesce(pos_name,'') not in ('','cashier') "
                      "returning id, store_id, pos_name")
        print("[接続の表示名クリア]", json.dumps(fixconn, ensure_ascii=False, default=str))
        # 統合で消えた店を指す接続の store_id を掃除（宙ぶらりん参照）。
        fixref = run("update public.store_pos set store_id=null "
                     "where store_id is not null and store_id not in (select id from public.stores) "
                     "returning id, store_id")
        print("[宙ぶらりん store_id をnull化]", json.dumps(fixref, ensure_ascii=False, default=str))
        return 0

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
