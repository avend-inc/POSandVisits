-- =====================================================================
--  sql/039_function_grants.sql
--  関数の実行権限を締める（未ログインでレジのパスワードが復号できた）
--  実行日: 2026-08-30 / 実行済み（Supabase MCP）
-- =====================================================================
--
-- 【見つかり方】
--   テーブルの権限を締めたあと、Supabase の security advisor を掛けたところ
--   「anon が SECURITY DEFINER 関数を25本実行できる」と出た。
--   テーブルを閉じても、SECURITY DEFINER 関数はRLSを迂回するので、
--   関数が空いていれば同じことになる。
--
-- 【実際に起きていたこと（最も重い）】
--   未ログイン（公開JSに埋まっている anon キーだけ）で
--     select public.get_pos_secret(1);
--   を叩くと、**レジ(POS)のパスワードが復号できた**。
--   sql/011 では revoke してあったが、その後に関数を CREATE OR REPLACE した
--   時点で PUBLIC への EXECUTE が付き直り、無効化されていた。
--   （Postgres は CREATE FUNCTION のたびに PUBLIC へ EXECUTE を既定で付ける）
--
-- 【入れたもの】
--   ・public / anon から public スキーマの全関数の EXECUTE を剥奪
--   ・authenticated / service_role には付け直す（アプリとRLSが関数を使うため）
--   ・レジのパスワードまわりは authenticated からも外す
--       get_pos_secret       … 復号。ETL(service_role)だけ
--       service_upsert_pos   … 登録。ETL(service_role)だけ
--   ・既定権限も変更。以後 CREATE OR REPLACE しても PUBLIC には付かない
--     （今回の再発の原因そのものを潰す）
--
-- 【実測】
--                         修正前 → 修正後
--   未ログインでPW復号    復号できた → 権限エラー
--   加盟オーナーでPW復号  −          → 権限エラー
--   社内(管理者)でPW復号  −          → 権限エラー（ETLだけが持つ）
--   service_role でPW復号 OK        → OK（取り込みは無傷）
--   is_hq() など判定関数  −          → 社内true/加盟false（アプリは動く）
--
-- 【戻し方】
--   grant execute on function public.get_pos_secret(bigint) to authenticated;
--   -- 既定権限を戻す場合:
--   alter default privileges in schema public grant execute on functions to public;
-- =====================================================================

revoke execute on all functions in schema public from public, anon;
grant  execute on all functions in schema public to authenticated, service_role;

revoke execute on function public.get_pos_secret(bigint) from authenticated;
revoke execute on function public.service_upsert_pos(bigint,text,text,text,text,text,boolean)
  from authenticated;

alter default privileges in schema public revoke execute on functions from public, anon;

-- 診断に残っていた RLS 無効のバックアップ表
alter table if exists public.bizplan_monthly_backup_20260830 enable row level security;
drop policy if exists zz_internal_only on public.bizplan_monthly_backup_20260830;
create policy zz_internal_only on public.bizplan_monthly_backup_20260830 for all to authenticated
  using (public.is_avend_internal()) with check (public.is_avend_internal());
revoke all on public.bizplan_monthly_backup_20260830 from anon;

notify pgrst, 'reload schema';

-- 確認:
--   set local role anon; select public.get_pos_secret(1);   -- 権限エラーになること
