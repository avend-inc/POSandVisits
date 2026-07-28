-- ============================================================
--  sql/011_pos_fetch.sql
--  自動取得(フェッチャ)のための追加。
--   ① store_pos.store_id を任意に（Siのように1アカウントで複数店を含む接続のため）。
--   ② ETL(service_role)がパスワードを復号して読むためのRPC get_pos_secret。
--      ※ anon/authenticated からは実行不可。service_role（サーバ専用キー）だけが呼べる。
--  前提: 010(store_pos)・supabase_vault 済み。何度実行しても安全。
-- ============================================================

-- ① Siは「アカウント全体（複数店）」の接続もあるので store_id を任意にする。
alter table public.store_pos alter column store_id drop not null;

-- ② サーバ専用キーだけがPWを復号して読める関数。
create or replace function public.get_pos_secret(p_id bigint)
returns text
language sql security definer set search_path = public, vault as $$
  select ds.decrypted_secret
    from public.store_pos sp
    join vault.decrypted_secrets ds on ds.id = sp.pw_secret_id
   where sp.id = p_id
$$;

-- 実行権限は service_role だけ（画面の anon/authenticated からは呼べない＝漏洩しない）。
revoke all on function public.get_pos_secret(bigint) from public;
revoke all on function public.get_pos_secret(bigint) from anon;
revoke all on function public.get_pos_secret(bigint) from authenticated;
grant execute on function public.get_pos_secret(bigint) to service_role;
