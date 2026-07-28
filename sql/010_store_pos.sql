-- ============================================================
--  sql/010_store_pos.sql
--  店舗ごとのレジ接続情報（レジ種類・URL・ID・PW）をアプリから登録できるようにする。
--  ★パスワードは Supabase Vault に「暗号化」して保存（復号鍵はテーブル外）。
--   store_pos には Vault の秘密ID(uuid)だけを持たせ、パスワード本体は保持しない。
--
--  【セキュリティ】
--   ・PWはVaultにAES暗号化で保存。DBダンプが漏れても平文は出ない。
--   ・画面(anon/authenticated)はPWにもVaultにも一切アクセスできない。
--   ・登録/更新/削除は SECURITY DEFINER のRPC経由。RPC内で「管理者(admin)のみ」を強制。
--   ・ETL は service_role キーで vault.decrypted_secrets を読んで復号し、各店にログインする。
--
--  【使い方】Supabase → SQL Editor に貼って [Run]。何度実行しても安全。
--  前提: 005(current_app_role / current_app_email)・004(stores) 実行済み。
--  ※ Vault拡張が無効だと create extension で有効化します（通常は既定で有効）。
-- ============================================================

create extension if not exists supabase_vault;

create table if not exists public.store_pos (
  id           bigserial primary key,
  store_id     bigint  not null references public.stores(id) on delete cascade,
  pos_type     text    not null check (pos_type in ('cashier','airregi','ezregi')),
  pos_name     text    not null default '',   -- 表示名（例：EZレジ・下北沢）。同一店で複数レジを区別
  url          text,                          -- レジWEB管理画面のURL（アカウントごとに異なる）
  login_id     text,
  pw_secret_id uuid,                          -- ★Vaultの秘密ID。PW本体はここには無い
  active       boolean not null default true,
  updated_at   timestamptz not null default now(),
  updated_by   text,
  unique (store_id, pos_type, pos_name)
);
create index if not exists store_pos_store_idx on public.store_pos (store_id);

alter table public.store_pos enable row level security;

-- 直接の書き込みは塞ぐ（登録/更新/削除はRPC経由のみ）。読み取りは管理者のみ。
revoke all on public.store_pos from anon;
revoke all on public.store_pos from authenticated;
grant select (id, store_id, pos_type, pos_name, url, login_id, active, updated_at, updated_by, pw_secret_id)
  on public.store_pos to authenticated;

drop policy if exists store_pos_admin_sel on public.store_pos;
create policy store_pos_admin_sel on public.store_pos
  for select to authenticated using (public.current_app_role() = 'admin');

-- --- 登録/更新：管理者のみ。PW(p_pw)が空なら既存PWは変更しない。 ----------------
create or replace function public.admin_upsert_pos(
  p_id       bigint,
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
  if public.current_app_role() <> 'admin' then
    raise exception '管理者のみ実行できます';
  end if;

  if p_id is null then
    insert into public.store_pos(store_id, pos_type, pos_name, url, login_id, active, updated_by)
      values (p_store_id, p_pos_type, coalesce(p_pos_name,''), p_url, p_login_id,
              coalesce(p_active,true), public.current_app_email())
      returning id into v_id;
  else
    v_id := p_id;
    update public.store_pos
       set store_id=p_store_id, pos_type=p_pos_type, pos_name=coalesce(p_pos_name,''),
           url=p_url, login_id=p_login_id, active=coalesce(p_active,true),
           updated_by=public.current_app_email(), updated_at=now()
     where id = v_id;
  end if;

  -- パスワードが入力されたときだけ Vault を作成/更新
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

-- --- 削除：管理者のみ。Vaultの秘密も一緒に消す。 -----------------------------------
create or replace function public.admin_delete_pos(p_id bigint)
returns void
language plpgsql security definer set search_path = public, vault as $$
declare v_secret uuid;
begin
  if public.current_app_role() <> 'admin' then
    raise exception '管理者のみ実行できます';
  end if;
  select pw_secret_id into v_secret from public.store_pos where id = p_id;
  delete from public.store_pos where id = p_id;
  if v_secret is not null then
    delete from vault.secrets where id = v_secret;
  end if;
end $$;

grant execute on function public.admin_upsert_pos(bigint,bigint,text,text,text,text,text,boolean) to authenticated;
grant execute on function public.admin_delete_pos(bigint) to authenticated;

-- 確認（任意）：PWは出ず、pw_secret_id が入っていれば「設定済み」。
-- select id, store_id, pos_type, pos_name, url, login_id, (pw_secret_id is not null) as pw_set from public.store_pos order by store_id;
