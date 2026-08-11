"""
Meta広告データ（meta_insights_daily / meta_sync_runs）の点検（読み取りのみ）。

    python -m tools.meta_probe

【何を見るか】
  ① 同期の実行記録 … いつ・どこから走っているか。実行時刻が毎日ほぼ同じなら
     自動（cron）、バラバラで日中だけなら手動/PC実行の疑い。
  ② 未紐付け … destination_id（店舗）が付いていない行がどれだけあるか。
     ここが多いと「店舗別の広告費」が実態より小さく出る。
  ③ カバレッジ … 直近の日で抜けている日が無いか。
  ④ アカウント別 … どのアカウントが、いつからいつまで、いくら使っているか。

  中身の生データは出さず、件数・合計・名称だけを出す。書き込みは一切しない。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""


def run(sql: str):
    if not REF or not TOKEN:
        raise SystemExit("SUPABASE_URL / SUPABASE_ACCESS_TOKEN が設定されていません。")
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": sql}, timeout=60)
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def ask(title: str, sql: str, cols=None) -> None:
    """1本ぶんを実行して出す。失敗しても他の項目は出したいので、ここで止めない。

    点検ツールは「何が起きているか知りたい」ときに動かすもの。1つのクエリが
    列の変更などでコケたせいで全部見られなくなるのがいちばん困る。
    """
    try:
        show(title, run(sql), cols)
    except SystemExit as e:
        print(f"\n===== {title} =====")
        print(f"  ⚠️ 取れませんでした: {str(e)[:300]}")


def show(title: str, rows, cols=None) -> None:
    print(f"\n===== {title} =====")
    if not rows:
        print("  （該当なし）")
        return
    cols = cols or list(rows[0].keys())
    print("  " + " | ".join(cols))
    for r in rows:
        print("  " + " | ".join(str(r.get(c)) for c in cols))


def main() -> int:
    # ① 同期の実行記録（JSTに直して表示）
    ask("同期の実行記録（直近15件・JST）", """
        select to_char(started_at at time zone 'Asia/Tokyo','MM/DD HH24:MI') as 開始JST,
               to_char(finished_at at time zone 'Asia/Tokyo','HH24:MI')      as 終了,
               since::text as 取得開始, until::text as 取得終了,
               rows_upserted as 件数, accounts_ok as 成功ac, accounts_failed as 失敗ac,
               unmapped_campaigns as 未紐付, status,
               left(coalesce(message,''),40) as message
          from meta_sync_runs order by started_at desc limit 15
    """)

    ask("実行時刻の分布（JSTの時間帯ごとの回数）", """
        select extract(hour from started_at at time zone 'Asia/Tokyo')::int as 時,
               count(*) as 回数
          from meta_sync_runs group by 1 order by 1
    """)

    # ② 未紐付け
    ask("全体サマリ", """
        select count(*) as 行数,
               min(date)::text as 最古, max(date)::text as 最新,
               count(*) filter (where destination_id is null) as 未紐付行数,
               round(100.0 * count(*) filter (where destination_id is null) / nullif(count(*),0), 1) as 未紐付率pct,
               round(sum(spend))::bigint as 消化額合計,
               round(sum(spend) filter (where destination_id is null))::bigint as 未紐付の消化額
          from meta_insights_daily
    """)

    ask("未紐付けキャンペーン（消化額の多い順・上位20）", """
        select coalesce(account_name,'(不明)') as アカウント,
               coalesce(campaign_name,'(名称なし)') as キャンペーン,
               count(*) as 行数,
               min(date)::text as 最古, max(date)::text as 最新,
               round(sum(spend))::bigint as 消化額
          from meta_insights_daily
         where destination_id is null
         group by 1,2 order by 6 desc nulls last limit 20
    """)

    # ②-2 アクション系（follows / profile_visits）の入り具合
    #   列があっても値が入っていなければ画面は「—」のまま。ここで見分ける。
    ask("アクション系の取込状況（follows / profile_visits）", """
        select count(*) as 行数,
               count(follows)        as follows入り,
               count(profile_visits) as profile_visits入り,
               min(date) filter (where follows is not null)::text        as follows最古,
               max(date) filter (where follows is not null)::text        as follows最新,
               min(date) filter (where profile_visits is not null)::text as pv最古,
               max(date) filter (where profile_visits is not null)::text as pv最新,
               sum(follows)        as follows合計,
               sum(profile_visits) as pv合計
          from meta_insights_daily
    """)

    ask("アクション系が入っている日（直近14日ぶん）", """
        select date::text as 日,
               count(*) as 行数,
               count(follows) as follows行, sum(follows) as follows,
               count(profile_visits) as pv行, sum(profile_visits) as pv
          from meta_insights_daily
         where date >= current_date - interval '14 day'
         group by 1 order by 1 desc
    """)

    # ③ 直近30日のカバレッジ（データが無い日）
    ask("直近30日で行が無い日（欠測日）", """
        select d::date::text as 欠測日
          from generate_series(current_date - interval '30 day', current_date - interval '1 day', interval '1 day') d
         where not exists (select 1 from meta_insights_daily m where m.date = d::date)
         order by 1
    """)

    # ④ アカウント別
    ask("アカウント別（消化額の多い順）", """
        select coalesce(account_name,'(不明)') as アカウント,
               count(distinct campaign_id) as キャンペーン数,
               min(date)::text as 最古, max(date)::text as 最新,
               round(sum(spend))::bigint as 消化額,
               round(100.0 * count(*) filter (where destination_id is not null) / nullif(count(*),0), 1) as 紐付率pct
          from meta_insights_daily
         group by 1 order by 5 desc nulls last limit 20
    """)

    # 紐付いている店舗（名前は destinations から。無ければ id だけ）
    ask("紐付いている店舗（消化額の多い順・上位20）", """
        select coalesce(d.name, m.destination_id::text) as 店舗,
               count(distinct m.campaign_id) as キャンペーン数,
               round(sum(m.spend))::bigint as 消化額
          from meta_insights_daily m
          left join destinations d on d.id = m.destination_id
         where m.destination_id is not null
         group by 1 order by 3 desc nulls last limit 20
    """)

    # ④-2 納品先 → POS店舗 の結び付き
    #   広告は destinations（納品先）に紐付くが、売上・来店は POS店舗にある。
    #   その2つは pos_store_links でつながる。ここが欠けている納品先は
    #   「広告費は出るのに売上比率が出ない」店になる。
    ask("納品先とPOS店舗の結び付き（消化額の多い順）", """
        select coalesce(d.name, m.destination_id::text) as 納品先,
               case when l.pos_store_id is null then '❌ つながっていない' else 'OK' end as 結び付き,
               coalesce(s.name,'-') as POS店舗,
               round(sum(m.spend))::bigint as 消化額
          from meta_insights_daily m
          left join destinations   d on d.id = m.destination_id
          left join pos_store_links l on l.destination_id = m.destination_id
          left join stores         s on s.id = l.pos_store_id
         where m.destination_id is not null
         group by 1,2,3 order by 4 desc nulls last limit 30
    """)

    # ④-3 広告を出している店に、POSの売上・来店が入っているか
    #   結び付き(pos_store_links)があっても、POSの取り込みが動いていなければ
    #   売上比率は出ない。新店で取り込み待ちなのか、設定漏れなのかを見分ける。
    ask("広告を出している店のPOS売上・来店（直近90日）", """
        with adstores as (
          select distinct l.pos_store_id as sid
            from meta_insights_daily m
            join pos_store_links l on l.destination_id = m.destination_id
           where m.date >= current_date - interval '90 day'
        )
        select s.name as 店舗,
               coalesce(sa.n,0) as 売上明細行, sa.first::text as 売上初日, sa.last::text as 売上最終日,
               coalesce(v.n,0)  as 来店行,     v.last::text  as 来店最終日
          from adstores a
          join stores s on s.id = a.sid
          left join (select store_id, count(*) n, min(business_date) first, max(business_date) last
                       from sales where business_date >= current_date - interval '90 day'
                      group by 1) sa on sa.store_id = s.id
          left join (select store_id, count(*) n, max(business_date) last
                       from visits where business_date >= current_date - interval '90 day'
                      group by 1) v on v.store_id = s.id
         order by coalesce(sa.n,0), s.name
         limit 30
    """)

    # ④-4 来店が取れていない店は「未導入（仕様）」か「設定漏れ・停止」か
    #   digitel_slug が空 … 取りにいく先が無い＝未導入
    #   kpi_visitors=false … 来店をKPIに使わない設定
    #   どちらでもないのに来店が無い／少ない＝取り込みが止まっている疑い
    ask("来店の取れ具合と設定（広告を出している店）", """
        with adstores as (
          select distinct l.pos_store_id as sid
            from meta_insights_daily m
            join pos_store_links l on l.destination_id = m.destination_id
           where m.date >= current_date - interval '90 day'
        )
        select s.name as 店舗,
               case when coalesce(btrim(s.digitel_slug),'')='' then '空（未導入）'
                    else s.digitel_slug end as slug,
               s.kpi_visitors as KPIに使う,
               coalesce(v.n,0) as 来店行90日, v.last::text as 来店最終日
          from adstores a
          join stores s on s.id = a.sid
          left join (select store_id, count(*) n, max(business_date) last
                       from visits where business_date >= current_date - interval '90 day'
                      group by 1) v on v.store_id = s.id
         order by coalesce(v.n,0), s.name
         limit 30
    """)

    # ④-5 画面の「日別の推移」が短く出る件の確認
    #   直近4週（2026-07-13〜08-09）で、福井の広告費が日ごとに入っているか。
    #   DBに日が揃っているなら画面側の不具合、DBが飛び飛びならデータ側。
    ask("福井の日別広告費（2026-07-13〜2026-08-09）", """
        select m.date::text as 日,
               round(sum(m.spend))::bigint as 広告費,
               count(*) as 行数
          from meta_insights_daily m
          left join destinations d on d.id = m.destination_id
         where d.name like '%福井%'
           and m.date between date '2026-07-13' and date '2026-08-09'
         group by 1 order by 1
    """)

    ask("福井の月別広告費と日数", """
        select to_char(m.date,'YYYY-MM') as 月,
               count(distinct m.date) as 日数,
               round(sum(m.spend))::bigint as 広告費,
               min(m.date)::text as 最古, max(m.date)::text as 最新
          from meta_insights_daily m
          left join destinations d on d.id = m.destination_id
         where d.name like '%福井%'
         group by 1 order by 1 desc limit 6
    """)

    # ⑤ 広告(ad)単位のテーブルがあれば、そちらの状況も見る
    ad = run("""
        select count(*)::int as n from information_schema.tables
         where table_schema='public' and table_name='meta_insights_daily_ad'
    """)
    if ad and (ad[0].get("n") or 0) > 0:
        cols = {r["column_name"] for r in run("""
            select column_name from information_schema.columns
             where table_schema='public' and table_name='meta_insights_daily_ad'
        """)}
        extra = "".join(
            f", count({c}) as {c}入り, sum({c}) as {c}合計"
            for c in ("follows", "profile_visits") if c in cols)
        show("広告(ad)単位テーブルの状況", run(f"""
            select count(*) as 行数, min(date)::text as 最古, max(date)::text as 最新,
                   count(*) filter (where destination_id is null) as 未紐付行数,
                   round(sum(spend))::bigint as 消化額合計{extra}
              from meta_insights_daily_ad
        """))
        if not extra:
            print("  ※ follows / profile_visits の列がまだありません（ad単位）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
