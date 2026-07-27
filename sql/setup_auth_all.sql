-- =====================================================================
--  NOTIME ログイン制セットアップ（003＋004＋005を1つにまとめたもの）
-- =====================================================================
--  ★これ1つを SQL Editor に貼って [Run] するだけで、下記3つが一度に入ります：
--    003_dashboard_auth.sql  … data.json を社内だけ読める非公開に
--    004_store_ownership.sql … 店舗に「直営/FC」区分を追加
--    005_auth_roles.sql      … 権限(閲覧/編集/管理)・ユーザー/店舗管理
--
--  ・何度実行しても安全（既にあるものは作り直し/スキップ）。
--  ・service_role(GitHub ActionsのETL)は RLS を素通りするので日次取込に影響なし。
--  ・実行後、sho.nakano@avend.co.jp が「管理者」として登録されます。
-- =====================================================================

-- ---------------------------------------------------------------------
-- [1/3] 003 ダッシュボードdata.jsonを社内だけ読める非公開に
-- ---------------------------------------------------------------------
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

-- ---------------------------------------------------------------------
-- [2/3] 004 店舗に「直営/FC」区分を追加
-- ---------------------------------------------------------------------
alter table stores
  add column if not exists ownership text not null default '直営';

-- 値は '直営' か 'FC' のどちらか（表記ゆれ防止）。
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'stores_ownership_chk'
  ) then
    alter table stores
      add constraint stores_ownership_chk check (ownership in ('直営','FC'));
  end if;
end $$;

-- 確認（任意）
-- select id, name, ownership from stores order by id;

-- ---------------------------------------------------------------------
-- [3/3] 005 権限(閲覧/編集/管理)・ユーザー/店舗管理
-- ---------------------------------------------------------------------
create or replace function public.current_app_email() returns text
  language sql stable security definer set search_path = public as $$
  select lower(coalesce(auth.jwt() ->> 'email', ''))
$$;

create or replace function public.current_app_role() returns text
  language sql stable security definer set search_path = public as $$
  select role from public.app_users where email = public.current_app_email() limit 1
$$;

-- --- ユーザー表（email → ロール）-------------------------------------------
create table if not exists public.app_users (
  email      text primary key,
  name       text,
  role       text not null default 'viewer' check (role in ('viewer','editor','admin')),
  created_at timestamptz not null default now()
);
alter table public.app_users enable row level security;

-- 読み取り：社内(@avend.co.jp)でログインしていれば可（自分のロール確認・管理一覧のため）
drop policy if exists "app_users_read" on public.app_users;
create policy "app_users_read" on public.app_users
  for select to authenticated
  using ( public.current_app_email() like '%@avend.co.jp' );

-- 追加/変更/削除：管理(admin)ロールのみ
drop policy if exists "app_users_admin_write" on public.app_users;
create policy "app_users_admin_write" on public.app_users
  for all to authenticated
  using ( public.current_app_role() = 'admin' )
  with check ( public.current_app_role() = 'admin' );

-- 最初の管理者を登録（Shoさん）。既にいれば admin に更新。
insert into public.app_users(email, name, role)
  values ('sho.nakano@avend.co.jp', 'Sho Nakano', 'admin')
  on conflict (email) do update set role = 'admin';

-- --- 予算・コスト等の入力データ（サーバ共有・端末localStorageから移行）------
--   page='pl'（月次予実）/ 'forecast'（着地見込み）、scope=店舗id文字列 or 'all'
create table if not exists public.plan_data (
  page       text not null,
  scope      text not null,
  ym         text not null,
  data       jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  updated_by text,
  primary key (page, scope, ym)
);
alter table public.plan_data enable row level security;

-- 読み取り：社内でログインしていれば可（閲覧ロールも数字は見える）
drop policy if exists "plan_read" on public.plan_data;
create policy "plan_read" on public.plan_data
  for select to authenticated
  using ( public.current_app_email() like '%@avend.co.jp' );

-- 書き込み：編集(editor)・管理(admin)のみ
drop policy if exists "plan_write" on public.plan_data;
create policy "plan_write" on public.plan_data
  for all to authenticated
  using ( public.current_app_role() in ('editor','admin') )
  with check ( public.current_app_role() in ('editor','admin') );

-- --- 店舗マスタ：閲覧は社内ログイン、追加・変更は管理のみ --------------------
alter table public.stores enable row level security;

drop policy if exists "stores_read" on public.stores;
create policy "stores_read" on public.stores
  for select to authenticated
  using ( public.current_app_email() like '%@avend.co.jp' );

drop policy if exists "stores_admin_write" on public.stores;
create policy "stores_admin_write" on public.stores
  for all to authenticated
  using ( public.current_app_role() = 'admin' )
  with check ( public.current_app_role() = 'admin' );

-- --- 売上/来店/ログは画面(anonキー)から直接触らせない ----------------------
--   ダッシュボードは data.json 経由。ここは service_role(ETL) だけが読み書きする。
--   RLSを有効化しつつポリシーを作らない＝anon/authenticated からは一切見えない。
alter table public.sales      enable row level security;
alter table public.visits     enable row level security;
alter table public.ingest_log enable row level security;

-- 確認（任意）
-- select email, role from public.app_users order by role;

