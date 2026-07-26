-- =====================================================================
--  ダッシュボードを「社内(@avend.co.jp)のGoogleログイン制」にするための設定
-- =====================================================================
--
-- 【これは何？】
--   ダッシュボードの実データ(data.json)を、Supabase Storage の
--   非公開バケット 'dashboard-data' に置き、
--   「@avend.co.jp の Google でログインした人だけが読める」ようにする。
--
-- 【使い方】
--   Supabase 管理画面 → SQL Editor に、この中身をまるごと貼って [Run]。
--   （config.py / adapters.py などのコードは触りません）
--
-- 【前提】
--   ・非公開バケット 'dashboard-data' は tools/export_dashboard.py が
--     自動で作ります（先に作っておく必要はありません）。
--   ・Supabase の Authentication → Providers で Google を有効化し、
--     Authentication → URL Configuration にダッシュボードURLを登録しておくこと
--     （手順は README / セットアップ手順を参照）。
--
-- 【安全性メモ】
--   ・ここで許可するのは「読み取り(SELECT/ダウンロード)」だけ。
--   ・書き込み(アップロード)は service_role キー(GitHub Actions)だけが行う。
--     service_role は RLS を素通りするので、下のポリシーの対象外＝影響なし。
-- =====================================================================

-- storage.objects には既定でRLSが有効。ここに「読み取り許可」を1本足す。
-- 同名ポリシーが既にある場合に備えて、まず消してから作り直す。
drop policy if exists "dashboard-data readable by avend members" on storage.objects;

create policy "dashboard-data readable by avend members"
  on storage.objects
  for select
  to authenticated
  using (
    bucket_id = 'dashboard-data'
    and lower(coalesce(auth.jwt() ->> 'email', '')) like '%@avend.co.jp'
  );

-- 確認用（任意）：作成されたポリシーを一覧表示
-- select policyname, cmd, roles
--   from pg_policies
--  where schemaname = 'storage' and tablename = 'objects';
