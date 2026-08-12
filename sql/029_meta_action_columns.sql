-- =====================================================================
--  sql/029_meta_action_columns.sql
--  Meta広告に「いいね数」と「平均視聴時間」の列を足す
-- =====================================================================
--
-- 【なぜ】
--   店舗ページの広告カードで「いいね率」「平均視聴時間」を見たいが、
--   いま meta_insights_daily に入っているのは 広告費・表示回数・リーチ・クリック だけ。
--   フォロー数・プロフアクセス数の列（follows / profile_visits）は既にあるが、
--   いいね数と平均視聴時間の置き場が無い。
--
-- 【どこから取るか】
--   いいね数     … insights の actions 配列（action_type = post_reaction ほか）
--   平均視聴時間 … insights の video_avg_time_watched_actions（秒）
--   どちらも etl/meta_actions_fetch.py が取り込む。
--   ※ 取り込みには META_ACCESS_TOKEN（ads_read）の登録が要る。
--     登録するまでは列が NULL のままで、画面には「—」と出る（0とは区別される）。
--
-- 【平均視聴時間の扱い】
--   これは「その日・そのキャンペーンの平均秒数」であって、足せる値ではない。
--   期間や店舗でまとめるときは、画面側で表示回数の重み付き平均にしている。
--   DBには生の平均秒数をそのまま置く。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
-- =====================================================================

alter table public.meta_insights_daily add column if not exists likes integer;
alter table public.meta_insights_daily add column if not exists video_avg_seconds numeric;

comment on column public.meta_insights_daily.likes is
  'いいね数（insights の actions／post_reaction ほか）。NULL＝未取得';
comment on column public.meta_insights_daily.video_avg_seconds is
  '平均視聴時間の秒数（video_avg_time_watched_actions）。行ごとの平均なので足さないこと';

-- 広告（クリエイティブ）単位にも同じ列を置く。どの動画が見られているかを比べるため
alter table public.meta_insights_daily_ad add column if not exists video_avg_seconds numeric;
comment on column public.meta_insights_daily_ad.video_avg_seconds is
  '平均視聴時間の秒数（video_avg_time_watched_actions）。行ごとの平均なので足さないこと';

notify pgrst, 'reload schema';
