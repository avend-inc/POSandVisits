-- ============================================================
--  sql/015_service_upsert_pos.sql
--  レジ接続の「一括登録」用RPC（service_role専用）。
--  管理画面の admin_upsert_pos は“ログイン中の管理者”前提だが、一括取り込みは
--  GitHub Actions が service_role キーで動く（ユーザーJWTが無い）ため、専用の
--  service_role 限定RPCを用意する。PWは admin版と同じく Vault に暗号化保存。
--
--  【セキュリティ】anon/authenticated には実行権を与えない（service_role のみ）。
--   一意キー (store_id, pos_type, pos_name) で upsert。PWが空なら既存を変更しない。
--  前提: 010(store_pos, Vault) 実行済み。何度実行しても安全。
-- ============================================================
create or replace function public.service_upsert_pos(
  p_store_id bigint,
  p_pos_type text,
  p_pos_name text,
  p_url      text,
  p_login_id text,
  p_pw       text,
  p_active   boolean
) returns bigint
language plpgsql security definer set search_path = public, vault as $$
declare v_id bigint; v_secret uuid;
begin
  select id into v_id from public.store_pos
    where store_id = p_store_id and pos_type = p_pos_type
      and pos_name = coalesce(p_pos_name,'');
  if v_id is null then
    insert into public.store_pos(store_id, pos_type, pos_name, url, login_id, active, updated_by)
      values (p_store_id, p_pos_type, coalesce(p_pos_name,''), p_url, p_login_id,
              coalesce(p_active,true), 'bulk-import')
      returning id into v_id;
  else
    update public.store_pos
       set url = p_url, login_id = p_login_id, active = coalesce(p_active,true),
           updated_by = 'bulk-import', updated_at = now()
     where id = v_id;
  end if;

  if p_pw is not null and length(p_pw) > 0 then
    select pw_secret_id into v_secret from public.store_pos where id = v_id;
    if v_secret is null then
      v_secret := vault.create_secret(p_pw, 'store_pos_'||v_id, 'POS password for store_pos '||v_id);
      update public.store_pos set pw_secret_id = v_secret where id = v_id;
    else
      perform vault.update_secret(v_secret, p_pw);
    end if;
  end if;
  return v_id;
end $$;

revoke all on function public.service_upsert_pos(bigint,text,text,text,text,text,boolean) from anon, authenticated;
grant execute on function public.service_upsert_pos(bigint,text,text,text,text,text,boolean) to service_role;
