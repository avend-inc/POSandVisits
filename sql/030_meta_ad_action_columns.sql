-- =====================================================================
--  sql/030_meta_ad_action_columns.sql
--  Meta広告の「広告（クリエイティブ）単位」にもアクション系の列を足す
-- =====================================================================
--
-- 【なぜ】
--   画面の CPC は「広告費 ÷ プロフアクセス数」で統一した。
--   キャンペーン別はこの計算で出せる（meta_insights_daily に
--   profile_visits があるため）が、クリエイティブ別は
--   meta_insights_daily_ad に profile_visits の列が無く、出せない。
--   列が無いあいだ、クリエイティブ別のCPCは「—」と出る（0にはしない）。
--
-- 【どこから取るか】
--   etl/meta_actions_fetch.py が、Meta insights を level=ad でもう一度取りに行く。
--   キャンペーンぶんを広告に割り戻すことはできない（プロフアクセス数は人の数なので、
--   広告をまたいで同じ人を数えたぶんがずれる）。なので別々に取る。
--
--   突き合わせは「日付 × ad_name」。この表は ad_id を持っていないため。
--   ※ 同じ日に同じ名前の広告が2本あると、片方に寄る。運用上そうなったら
--     ad_id を持たせるところからやり直すこと。
--
-- 【この列が埋まるまでの見え方】
--   NULL のあいだは画面に「—」と出る。0 とは区別している
--   （0だと「本当に1回も無かった」と読めてしまうため）。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
-- =====================================================================

alter table public.meta_insights_daily_ad add column if not exists profile_visits integer;
alter table public.meta_insights_daily_ad add column if not exists follows integer;
alter table public.meta_insights_daily_ad add column if not exists likes integer;

comment on column public.meta_insights_daily_ad.profile_visits is
  'プロフアクセス数（insights の actions）。CPC＝広告費÷この値。NULL＝未取得';
comment on column public.meta_insights_daily_ad.follows is
  'フォロー数（insights の actions）。NULL＝未取得';
comment on column public.meta_insights_daily_ad.likes is
  'いいね数（insights の actions／post_reaction ほか）。NULL＝未取得';

-- 日付 × 広告名で書き戻すので、その組み合わせに索引を張る
create index if not exists meta_insights_daily_ad_date_name_idx
  on public.meta_insights_daily_ad (date, ad_name);

notify pgrst, 'reload schema';
