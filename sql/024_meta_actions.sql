-- =====================================================================
--  sql/024_meta_actions.sql
--  Meta広告に「フォロー数」「プロフアクセス数」の列を足す
-- =====================================================================
--
-- 【これは何？】
--   広告タブで CPF（＝広告費÷フォロー数）とフォロー率（＝フォロー数÷
--   プロフアクセス数）を出したいが、meta_insights_daily に元の実額が無い。
--   その2列を足す。取り込みは avend-meta-ads 側で行う（こちらは器だけ用意する）。
--
-- 【画面側】
--   ダッシュボードの集計は、この2列を「付きで取りに行き、無ければ基本の列だけで
--   取り直す」作りにしてある。列が入って値が埋まれば、画面の指標は自動で出る。
--   こちらでの追加作業は要らない。
--
-- 【取り込み側へのメモ】
--   ・Instagramのフォロー数は 2025-07-07 以降しか取得できない（Metaの仕様）。
--     それ以前は遡れないので、過去ぶんは NULL のままで構わない。
--   ・値が取れなかった日は 0 ではなく NULL にしてほしい。
--     0 を入れると「フォローが0だった日」と区別が付かず、CPFが誤って出る。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
-- =====================================================================

alter table public.meta_insights_daily
  add column if not exists follows        bigint,
  add column if not exists profile_visits bigint;

comment on column public.meta_insights_daily.follows        is 'その日の広告経由のフォロー数（Metaの仕様で2025-07-07以降のみ取得可）';
comment on column public.meta_insights_daily.profile_visits is 'その日の広告経由のInstagramプロフィールアクセス数（クリック数とは別物）';

-- 確認（任意）
--   select count(*) filter (where follows is not null)        as フォロー数あり,
--          count(*) filter (where profile_visits is not null) as プロフあり,
--          count(*) as 全行
--     from public.meta_insights_daily;
