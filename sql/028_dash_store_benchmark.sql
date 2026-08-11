-- =====================================================================
--  sql/028_dash_store_benchmark.sql
--  加盟店向けの「店舗情報」と「比較値」をテーブルに置く（③の最終段階）
-- =====================================================================
--
-- 【いまの作りと、その限界】
--   加盟店には store-<id>.json（自店の明細＋事前計算した比較値）を配っている。
--   他店の生データは含めていないので漏れはないが、守っているのは
--   「ETLが自店ぶんだけを切り出して別ファイルに書く」というアプリの作り方。
--   切り出しを一箇所間違えれば、他店のデータが混ざる。
--
-- 【これで何が変わるか】
--   同じ内容をテーブルに置き、RLS(can_view_store)で守る。
--   Postgres が行単位で弾くので、画面やETLの作り方に関係なく他店の行は返らない。
--
-- 【比較値をテーブルに置く理由】
--   加盟店は他店の行を読めない。だから「同ブランド上位5平均」のような
--   比較値を画面側で計算することはできない。ETLが計算して、その店に見せてよい
--   数字だけを置く。中身は今 store-<id>.json に焼き込んでいるものと同じ。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
--   前提: sql/019（can_view_store）、sql/027（dash_daily ほか）を実行済み。
-- =====================================================================

-- --- ① 画面が要る店舗情報だけを持つ ------------------------------------
--   stores を直接読ませない。stores は在庫アプリと共用で列も多く、
--   加盟店に見せてよい範囲がはっきりしないため。
create table if not exists public.dash_store (
  store_id     bigint primary key,
  name         text not null,
  ownership    text,              -- 直営 / FC
  brand_label  text,              -- 比較の見出しに使う（直営 / SELFURUGI 等）
  kpi_visitors boolean not null default true,
  visible      boolean not null default true,
  updated_at   timestamptz not null default now()
);

-- --- ② 比較値（プリセット期間 × 指標）----------------------------------
--   1行 = この店に見せる、ある期間・ある指標の比較値。
--   preset は画面のプリセット（prevday / thisweek / thismonth / lastweek / lastmonth）。
create table if not exists public.dash_benchmark (
  store_id   bigint  not null,
  preset     text    not null,
  metric     text    not null,   -- ex / v / pr / aov / qty / upt / aup
  value      numeric,            -- 比較対象の平均（該当なしは null）
  date_from  date,
  date_to    date,
  updated_at timestamptz not null default now(),
  primary key (store_id, preset, metric)
);

-- --- 権限：自分が見られる店の行だけ ------------------------------------
--   比較値は「その店に見せてよい数字」として作ってある（他店の生データではない）。
--   いま store-<id>.json に焼き込んでいるものと同じ範囲。
do $$
declare t text;
begin
  foreach t in array array['dash_store','dash_benchmark'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists "%s_read" on public.%I', t, t);
    execute format(
      'create policy "%s_read" on public.%I for select to authenticated '
      'using (public.can_view_store(store_id))', t, t);
  end loop;
end $$;

-- 確認（任意）
--   select count(*) from public.dash_store;      -- 期待 44 前後
--   select count(*) from public.dash_benchmark;  -- 期待 店舗数 × 5期間 × 7指標
