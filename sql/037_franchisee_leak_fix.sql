-- =====================================================================
--  sql/037_franchisee_leak_fix.sql
--  加盟店（FC）オーナーに見えてはいけないものが2つ見えていたのを塞ぐ
--  実行日: 2026-08-30 / 実行済み（Supabase MCP）
-- =====================================================================
--
-- 【経緯】
--   sql/019 で「加盟オーナーは自店だけ」を作ったが、その後に増えた
--   mgmt_report と Meta広告のビューが取り残されていた。
--   加盟オーナーを模したJWTで実測したところ、次の2つが全件読めた。
--
--   ① mgmt_report（経営レポート）
--      ポリシーが using ( current_app_role() is not null ) で、
--      「app_users に行があれば誰でも」だった。加盟オーナーも viewer として
--      行を持つので素通りしていた。
--
--   ② meta_ads_daily_by_* （Meta広告の集計ビュー6本）
--      ビューは security_invoker を付けないと**所有者の権限で動き、RLSを迂回する**。
--      未設定のまま anon / authenticated に select が付いていたため、
--      全店の広告費・配信実績6,157行が誰でも読めた。
--      画面はこのビューを直接読んでいない（data.json 経由）ので、権限を外す。
--
-- 【実測（加盟オーナーを模したJWT）】
--                        修正前 → 修正後
--   dash_daily            1店   → 1店      （元から正しい）
--   sales(13.3万行)        0    → 0        （元から正しい）
--   mgmt_report(1行)       1    → 0        ★直った
--   meta広告ビュー(6157行) 6157 → 権限エラー ★直った
--   本部(admin)は 経営1件・予実61件・43店 とも従来どおり読める
--
-- 【戻し方】
--   ① drop policy mgmt_report_read on public.mgmt_report;
--      create policy mgmt_report_read on public.mgmt_report
--        for select to authenticated using ( public.current_app_role() is not null );
--   ② grant select on public.meta_ads_daily_by_store to authenticated, anon;  -- 6本ぶん
--
-- 【中野さんへの共有が要る】
--   mgmt_report は中野さん側の表。スキーマ（列）は変えていないが、
--   読み取り条件を「登録者全員」→「本部のみ」に狭めている。
-- =====================================================================

-- --- ① 経営レポートは本部だけ ----------------------------------------
drop policy if exists mgmt_report_read on public.mgmt_report;
create policy mgmt_report_read on public.mgmt_report
  for select to authenticated
  using ( public.is_hq() );

-- --- ② Meta広告のビューはブラウザから読ませない ------------------------
--   ETL・export_dashboard は service_role で動くので影響しない。
revoke select on public.meta_ads_daily_by_store     from anon, authenticated;
revoke select on public.meta_ads_daily_by_adset     from anon, authenticated;
revoke select on public.meta_ads_daily_by_demo      from anon, authenticated;
revoke select on public.meta_ads_daily_by_placement from anon, authenticated;
revoke select on public.meta_ads_daily_by_region    from anon, authenticated;
revoke select on public.meta_ads_daily_by_creative  from anon, authenticated;

notify pgrst, 'reload schema';

-- 確認:
--   set local role authenticated;
--   set local request.jwt.claims = '{"role":"authenticated","email":"<加盟オーナー>"}';
--   select count(*) from public.mgmt_report;              -- 0 が正
--   select count(distinct store_id) from public.dash_daily; -- 自店の数だけ
