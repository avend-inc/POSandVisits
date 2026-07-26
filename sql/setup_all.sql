-- ============================================================
--  NOTIME 日次ETL — これ1枚で初期セットアップ完了
--  使い方: Supabase 管理画面 → 左メニュー「SQL Editor」→
--          「New query」→ この中身を全部貼り付け →「Run」（1回だけ）
--  中身は sql/001_schema.sql と sql/002_seed_stores.sql をまとめたものです。
--  何度実行しても壊れません（if not exists / on conflict で書いてあります）。
-- ============================================================

-- ===== ① テーブル定義（001_schema.sql）=====

-- ------------------------------------------------------------
-- 1) stores : 店舗マスタ
--    「山形」「いわき」…と、各サービス側での呼び名の対応表
-- ------------------------------------------------------------
create table if not exists public.stores (
    id             bigserial primary key,
    code           text        not null unique,   -- 英字キー: yamagata / iwaki ...
    name           text        not null unique,   -- 表示名: 山形 / いわき ...
    digitel_slug   text,                          -- デジテールのURLに使う名前
    cashier_label  text,                          -- cashier CSV の「伝票：店舗名」
    open_date      date,
    has_entry_data boolean     not null default true,
    created_at     timestamptz not null default now()
);

-- ------------------------------------------------------------
-- 2) sales : cashier の売上明細（1行 = 伝票の1明細）
--    同じ行を二度入れないための一意キーが命。
--    (store_id, pos_name, tx_id, line_no) が同じなら「同じ明細」とみなす。
-- ------------------------------------------------------------
create table if not exists public.sales (
    id            bigserial primary key,
    business_date date        not null,           -- 営業日（伝票：精算日付）
    store_id      bigint      not null references public.stores(id),
    pos_name      text        not null,           -- 'cashier'
    tx_id         text        not null,           -- 伝票（取引）番号
    line_no       integer     not null,           -- 伝票内の明細の並び順（0始まり）

    ts            timestamptz,                    -- 伝票の処理日時
    sales_ex_tax  numeric(14,2),                  -- 伝票の売上（税抜）
    sales_in_tax  numeric(14,2),                  -- 伝票の売上（税込）
    tx_qty        numeric(12,2),                  -- 伝票の購入点数

    line_category text,                           -- 明細の商品カテゴリ
    line_price    numeric(14,2),                  -- 明細の販売価格
    line_qty      numeric(12,2),                  -- 明細の点数
    line_amount   numeric(14,2),                  -- 明細の小計金額

    bundle_code   text,                           -- セット割コード（無ければ空）
    is_parent     boolean,
    is_child      boolean,
    is_normal     boolean,

    ingested_at   timestamptz not null default now(),

    constraint sales_natural_key unique (store_id, pos_name, tx_id, line_no)
);

create index if not exists sales_business_date_idx on public.sales (business_date);
create index if not exists sales_store_date_idx    on public.sales (store_id, business_date);

-- ------------------------------------------------------------
-- 3) visits : デジテールの来店（入店）数（1行 = 1店舗 × 1日）
-- ------------------------------------------------------------
create table if not exists public.visits (
    id              bigserial primary key,
    business_date   date        not null,
    store_id        bigint      not null references public.stores(id),
    source          text        not null default 'digitel',

    visitors        integer,                      -- 無人入店/解錠数
    repeat_visitors integer,                      -- 無人常連来店数
    purchases       integer,                      -- 無人購買回数
    purchase_rate   numeric(6,2),                 -- 無人購買率(%)

    ingested_at     timestamptz not null default now(),

    constraint visits_natural_key unique (business_date, store_id, source)
);

create index if not exists visits_business_date_idx on public.visits (business_date);

-- ------------------------------------------------------------
-- 4) ingest_log : 取り込みの実行記録
--    status:
--      success            … 正常に取り込んだ
--      failed             … 失敗した（取得エラー・形式違いなど）
--      rejected_duplicate … 同じ営業日を再取り込みしようとした（NG）
--      no_data            … 取りに行けたが対象データが0件だった
-- ------------------------------------------------------------
create table if not exists public.ingest_log (
    id             bigserial primary key,
    run_id         text        not null,          -- 1回の実行にひとつのID
    source         text        not null,          -- 'cashier' | 'digitel'
    business_date  date        not null,
    store_id       bigint      references public.stores(id),
    status         text        not null,
    rows_fetched   integer     not null default 0,-- CSVから読めた行数
    rows_inserted  integer     not null default 0,-- 実際に新規追加された行数
    rows_duplicate integer     not null default 0,-- 既にあって無視した行数
    message        text,
    started_at     timestamptz,
    finished_at    timestamptz not null default now(),

    constraint ingest_log_status_check
        check (status in ('success', 'failed', 'rejected_duplicate', 'no_data'))
);

-- ★ここが「同じ営業日を二度取り込ませない」仕掛け。
--   status='success' の行は (source, 営業日, 店舗) の組で1本しか作れない。
--   2回目を入れようとすると Supabase 側がエラーを返す → NGとして記録する。
create unique index if not exists ingest_log_success_uk
    on public.ingest_log (source, business_date, coalesce(store_id, -1))
    where status = 'success';

create index if not exists ingest_log_run_idx  on public.ingest_log (run_id);
create index if not exists ingest_log_date_idx on public.ingest_log (business_date desc);

-- ------------------------------------------------------------
-- 5) 参考ビュー : 日次の売上と来店数を店舗ごとに突き合わせる
--    （購買率などを見るときに便利。無くてもETLは動きます）
-- ------------------------------------------------------------
create or replace view public.daily_store_kpi as
select
    s.business_date,
    st.name                                as store,
    sum(s.line_amount)                     as line_amount_total,
    count(distinct s.tx_id)                as tx_count,
    v.visitors,
    case when coalesce(v.visitors, 0) > 0
         then round(count(distinct s.tx_id)::numeric * 100 / v.visitors, 2)
    end                                    as purchase_rate_pct
from public.sales s
join public.stores st on st.id = s.store_id
left join public.visits v
       on v.store_id = s.store_id
      and v.business_date = s.business_date
group by s.business_date, st.name, v.visitors;


-- ===== ② 店舗マスタ初期データ（002_seed_stores.sql）=====

insert into public.stores (code, name, digitel_slug, cashier_label, open_date, has_entry_data)
values
    ('yamagata',     '山形',   'notime_yamagata',     'NOTIME山形',   '2026-03-20', true),
    ('iwaki',        'いわき', 'notime_iwaki',        'NOTIMEいわき', '2026-04-25', true),
    ('fukui',        '福井',   'notime_fukui',        'NOTIME福井',   '2026-06-13', true),
    ('shimokitazawa','下北沢', 'notime_shimokitazawa', null,          '2026-01-01', false)
on conflict (code) do update set
    name           = excluded.name,
    digitel_slug   = excluded.digitel_slug,
    cashier_label  = excluded.cashier_label,
    open_date      = excluded.open_date,
    has_entry_data = excluded.has_entry_data;
