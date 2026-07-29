-- ============================================================
--  sql/012_bundle_master.sql
--  バンドル（セット割＝SALE）の「コード→名称」対応表。
--  cashierの取引明細には bundle_code(BUNDLE001…) しか入っていないため、
--  名称（山形店_6点20%OFF 等）はこの表から付ける。
--  ・更新はcashierの「バンドル」CSVを取り込む（tools/import_bundle_master.py）。
--  ・ダッシュボードには exporter が名称を data.json に埋め込む（画面は直接読まない）。
--  前提: 005(current_app_role) 実行済み。何度実行しても安全。
-- ============================================================
create table if not exists public.bundle_master (
  code       text primary key,
  name       text not null default '',
  category   text,
  updated_at timestamptz not null default now()
);

alter table public.bundle_master enable row level security;

-- 閲覧：登録メンバー（保険。実際は exporter が service_role で読む）
drop policy if exists bundle_master_read on public.bundle_master;
create policy bundle_master_read on public.bundle_master
  for select to authenticated using (public.current_app_role() is not null);

-- 変更：管理者のみ（取り込みは service_role でRLS素通り）
drop policy if exists bundle_master_admin on public.bundle_master;
create policy bundle_master_admin on public.bundle_master
  for all to authenticated
  using (public.current_app_role() = 'admin')
  with check (public.current_app_role() = 'admin');

-- 確認（任意）
-- select code, name from public.bundle_master order by code;
