-- =====================================================================
--  plan_data をリアルタイム配信の対象に追加（端末間リアルタイム反映のため）
-- =====================================================================
--
-- 【これは何？】
--   予算・コスト・見込みの入力(plan_data)を、ある端末で保存したら
--   他の端末の画面に自動反映させる「リアルタイム」を有効化する。
--   Supabase Realtime は publication(supabase_realtime) に載ったテーブルの
--   変更だけを配信するので、plan_data を追加する。
--   （閲覧の可否は従来どおり RLS で制御。登録者のみ受信できる）
--
-- 【使い方】
--   Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
-- =====================================================================

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'plan_data'
  ) then
    alter publication supabase_realtime add table public.plan_data;
  end if;
end $$;

-- 確認（任意）
-- select schemaname, tablename from pg_publication_tables where pubname='supabase_realtime';
