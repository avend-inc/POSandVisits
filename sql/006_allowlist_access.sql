-- =====================================================================
--  閲覧アクセスを「@avend.co.jp ドメイン」→「許可リスト方式」に変更
-- =====================================================================
--
-- 【これは何？】
--   これまで「@avend.co.jp のメールだけ閲覧可」だったのを、
--   「app_users（管理画面で追加した人）に登録されたメールなら誰でも閲覧可」に変更する。
--   → @avend.co.jp 以外（個人Gmail・FCの方など）も、管理画面で追加すればログイン・閲覧できる。
--   （追加・権限変更・削除・店舗編集などの「書き込み」は従来どおり編集/管理ロールのみ）
--
-- 【使い方】
--   Supabase 管理画面 → SQL Editor にこの中身を貼って [Run]。
--   （005 を実行済みの前提。何度実行しても安全）
--
-- 【ポイント】
--   current_app_role() は SECURITY DEFINER なので app_users を素通りで参照でき、
--   「登録済み（＝ロールがある）＝許可リストに居る」を is not null で判定できる。
-- =====================================================================

-- app_users 読み取り：登録済みユーザー（自分のロール確認・一覧のため）
drop policy if exists "app_users_read" on public.app_users;
create policy "app_users_read" on public.app_users
  for select to authenticated
  using ( public.current_app_role() is not null );

-- plan_data 読み取り：登録済みユーザー（閲覧ロールも数字は見える）
drop policy if exists "plan_read" on public.plan_data;
create policy "plan_read" on public.plan_data
  for select to authenticated
  using ( public.current_app_role() is not null );

-- stores 読み取り：登録済みユーザー
drop policy if exists "stores_read" on public.stores;
create policy "stores_read" on public.stores
  for select to authenticated
  using ( public.current_app_role() is not null );

-- data.json（非公開バケット dashboard-data）読み取り：登録済みユーザー
drop policy if exists "dashboard-data readable by avend members" on storage.objects;
drop policy if exists "dashboard-data readable by members" on storage.objects;
create policy "dashboard-data readable by members" on storage.objects
  for select to authenticated
  using ( bucket_id = 'dashboard-data' and public.current_app_role() is not null );

-- 確認（任意）
-- select email, role from public.app_users order by role;
