-- =====================================================================
--  加盟店（FC）向け：Gmailごとに「見られる店舗」を制限するアクセス制御
-- =====================================================================
--
-- 【これは何？】
--   これまでは「app_users に登録された人は全店の data.json を読める」でした。
--   これを、
--     ・本部（app_user_stores に割り当てが無い登録ユーザー）＝全店を見られる（従来どおり）
--     ・加盟店（app_user_stores に割り当てがあるGmail）＝割り当てられた店舗だけ見られる
--   に変更します。
--
--   実データは店舗ごとに分割して配信します：
--     ・全店ぶん             … dashboard-data/data.json         → 本部だけ読める
--     ・店舗ごと（自店＋比較値） … dashboard-data/store-<id>.json  → その店の割当者＋本部だけ読める
--   （store-<id>.json は tools/export_dashboard.py が書き出します。中身は「自店の明細＋
--     匿名化した比較集計（同ブランド平均等の“数字”のみ）」で、他店の生データは含みません。）
--
-- 【使い方】
--   Supabase 管理画面 → SQL Editor にこの中身をまるごと貼って [Run]。
--   005 / 006 を実行済みの前提。何度実行しても安全（idempotent）。
--
-- 【安全性の要】
--   ・UIで店舗を隠すだけでは不十分（通信を見れば全店データが読めてしまう）。
--     ここで「データそのもの」をRLSで店舗単位に閉じるのが本質です。
--   ・書き込み（アップロード）は service_role（GitHub Actions）だけ。RLSを素通りするので影響なし。
--   ・後方互換：既存ユーザーは app_user_stores に行が無い＝本部扱い＝従来どおり全店閲覧。
-- =====================================================================

-- --- 割り当てテーブル：email → 見られる店舗id ------------------------------
create table if not exists public.app_user_stores (
  email      text   not null,
  store_id   bigint not null references public.stores(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (email, store_id)
);
alter table public.app_user_stores enable row level security;

-- --- 判定関数（SECURITY DEFINER：ポリシーの再帰を避けて app_user_stores を参照）----
-- 本部か？＝登録済み かつ 割り当て行が「無い」ユーザー（＝全店閲覧）。
create or replace function public.is_hq() returns boolean
  language sql stable security definer set search_path = public as $$
  select public.current_app_role() is not null
     and not exists (
       select 1 from public.app_user_stores
       where email = public.current_app_email()
     )
$$;

-- この店舗を閲覧できるか？（本部は全店 true。加盟店は割り当てられた店だけ true）
create or replace function public.can_view_store(sid bigint) returns boolean
  language sql stable security definer set search_path = public as $$
  select public.is_hq()
      or exists (
        select 1 from public.app_user_stores
        where email = public.current_app_email() and store_id = sid
      )
$$;

-- --- app_user_stores のRLS：自分の割当は読める／変更は管理者のみ ------------
drop policy if exists "app_user_stores_read" on public.app_user_stores;
create policy "app_user_stores_read" on public.app_user_stores
  for select to authenticated
  using ( email = public.current_app_email() or public.current_app_role() = 'admin' );

drop policy if exists "app_user_stores_admin_write" on public.app_user_stores;
create policy "app_user_stores_admin_write" on public.app_user_stores
  for all to authenticated
  using ( public.current_app_role() = 'admin' )
  with check ( public.current_app_role() = 'admin' );

-- --- stores：本部は全店、加盟店は自分の店だけ（全店リストの漏れを防ぐ）-------
drop policy if exists "stores_read" on public.stores;
create policy "stores_read" on public.stores
  for select to authenticated
  using ( public.can_view_store(id) );

-- --- app_users：管理者は全員、それ以外は自分の行だけ（他ユーザーの漏れを防ぐ）--
drop policy if exists "app_users_read" on public.app_users;
create policy "app_users_read" on public.app_users
  for select to authenticated
  using ( public.current_app_role() = 'admin' or email = public.current_app_email() );

-- --- plan_data（予実・着地見込み等の入力）：本部のみ読める（加盟店は対象外）----
drop policy if exists "plan_read" on public.plan_data;
create policy "plan_read" on public.plan_data
  for select to authenticated
  using ( public.is_hq() );
-- 書き込みは従来どおり editor/admin（005 の plan_write を維持）。

-- --- Storage：全店data.jsonは本部のみ／店舗バンドルは割当者＋本部 -------------
--   （旧ポリシーは名前を変えて作り直す。permissive な複数SELECTポリシーはORで合成される）
drop policy if exists "dashboard-data readable by members"       on storage.objects;
drop policy if exists "dashboard-data readable by avend members" on storage.objects;
drop policy if exists "dashboard-data hq full"                   on storage.objects;
drop policy if exists "dashboard-data store bundles"             on storage.objects;

-- 全店 data.json：本部だけ
create policy "dashboard-data hq full" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'dashboard-data'
    and name = 'data.json'
    and public.is_hq()
  );

-- 店舗バンドル store-<id>.json：本部 or その店の割当者
create policy "dashboard-data store bundles" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'dashboard-data'
    and name like 'store-%.json'
    and (
      public.is_hq()
      or exists (
        select 1 from public.app_user_stores aus
        where aus.email = public.current_app_email()
          and 'store-' || aus.store_id || '.json' = storage.objects.name
      )
    )
  );

-- =====================================================================
-- 確認（任意）：
--   -- 自分は本部か？
--   select public.is_hq();
--   -- 加盟店を1件割り当てる例（メールは事前に app_users にも登録しておくこと）：
--   --   insert into public.app_users(email,name,role) values ('owner@example.com','FCオーナー','viewer')
--   --     on conflict (email) do nothing;
--   --   insert into public.app_user_stores(email,store_id) values ('owner@example.com', 24)
--   --     on conflict do nothing;   -- 24 = 対象店舗id（stores.id）
--   -- 割り当て一覧：
--   select aus.email, s.name from public.app_user_stores aus join public.stores s on s.id=aus.store_id order by 1,2;
-- =====================================================================
