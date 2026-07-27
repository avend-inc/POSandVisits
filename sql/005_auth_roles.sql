-- =====================================================================
--  ユーザー権限（ロール）とユーザー/店舗管理のための設定
-- =====================================================================
--
-- 【これは何？】
--   ・ログインしたユーザーに「閲覧 / 編集 / 管理」のロールを持たせる。
--   ・予算やコストなどの入力を、端末ではなくサーバ(Supabase)に保存し、
--     編集は「編集・管理」ロールだけが書けるようにする（RLSでサーバ側強制）。
--   ・管理画面（ユーザー/店舗の追加・変更）は「管理」ロールだけが操作できる。
--
-- 【使い方】
--   Supabase 管理画面 → SQL Editor にこの中身をまるごと貼って [Run]。
--   （service_role キー＝GitHub Actions は RLS を素通りするので、日次ETLに影響なし）
--
-- 【前提】
--   ・Authentication → Providers で Google を有効化し、URL設定を済ませておく
--     （手順は別途のセットアップガイド参照）。
--   ・sql/003_dashboard_auth.sql（非公開data.jsonの読み取りRLS）も実行しておく。
-- =====================================================================

-- --- 現在ログイン中ユーザーの email / role を安全に取得する関数 -------------
--   SECURITY DEFINER で app_users を参照するので、ポリシーの再帰を避けられる。
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
