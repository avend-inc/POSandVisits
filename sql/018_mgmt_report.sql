-- ============================================================
--  sql/018_mgmt_report.sql
--  経営レポート（着地見込・ロイヤリティ・卸売上など）の編集値を月ごとに保存する。
--  ・1行 = 対象月(ym: YYYY-MM)。payload(jsonb) に画面の入力値一式をまとめて保存。
--  ・読み取り: 権限のある全員。書き込み: 編集(editor)・管理(admin)のみ（plan_data と同じ）。
--  前提: 005(current_app_role) 実行済み。何度実行しても安全。
-- ============================================================
create table if not exists public.mgmt_report (
  ym         text primary key,                       -- 対象月 YYYY-MM
  payload    jsonb not null default '{}'::jsonb,      -- 経営レポートの編集値一式
  updated_at timestamptz not null default now(),
  updated_by text
);

alter table public.mgmt_report enable row level security;

drop policy if exists "mgmt_report_read" on public.mgmt_report;
create policy "mgmt_report_read" on public.mgmt_report
  for select to authenticated
  using ( public.current_app_role() is not null );

drop policy if exists "mgmt_report_write" on public.mgmt_report;
create policy "mgmt_report_write" on public.mgmt_report
  for all to authenticated
  using ( public.current_app_role() in ('editor','admin') )
  with check ( public.current_app_role() in ('editor','admin') );

-- 確認（任意）
-- select ym, updated_at, updated_by from public.mgmt_report order by ym desc;
