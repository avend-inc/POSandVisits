-- =====================================================================
--  売上をデジテールから取る店を指定する列 digitel_sales
-- =====================================================================
--
-- 【これは何？】
--   POS（cashier / EZレジ 等）が無く、売上がデジテールにしか無い店
--   （例: NOTIME伊予松前）の売上を、デジテールの売上CSVから取り込むための
--   フラグ。true の店だけ、日次ETL・backfill がデジテール売上を sales に入れる。
--   （POSがある店で true にすると二重計上になるので注意）
--
-- 【使い方】
--   Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
-- =====================================================================

alter table public.stores
  add column if not exists digitel_sales boolean not null default false;

-- 伊予松前だけ、売上をデジテールから取る
update public.stores
   set digitel_sales = true
 where digitel_slug = 'notime_masakicho';

-- 確認（任意）
-- select name, code, digitel_slug, digitel_sales from public.stores where digitel_sales;
