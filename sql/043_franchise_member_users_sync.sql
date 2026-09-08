-- =====================================================================
--  sql/043_franchise_member_users_sync.sql
--  店舗ページ「アカウント管理」(franchise_add/remove_member) を
--  本番のログイン入口 public.users と同期させる。
--
--  【背景 / なぜ要ったか】
--   ログインの許可判定（web dashboard / 本番 avend-inventory の /sales/index.html）は
--   public.users を見る：is_active かつ segments に 'sales' を含む人だけ通す。
--   ところが従来の franchise_add_member は app_users と app_user_stores にしか
--   書かず users に載せないため、「追加しても本人はログインできない
--  （アクセス権がありません）」状態だった。ここで users にも登録するよう直す。
--
--  【安全設計】
--   ・users は RLS で「本部(can_grant)/admin のみ書込」。本関数は postgres 所有の
--     SECURITY DEFINER なので RLS を素通りして users を作成/更新できる。
--   ・保護列(segments 等)の変更は guard トリガ(guard_user_permission_columns)が
--     本部(is_avend_granter)以外を弾く。加盟オーナー操作では新規INSERT（is_internal=false）
--     のみ行い、既存ユーザーの segments 追記は本部操作時だけに限定してガードに触れない。
--   ・remove では「最後の1店を外す＝割当ゼロ」の人を users からも取り消す。
--     取り消さないと is_hq()=（role非null かつ 割当ゼロ）で本部扱い＝全店閲覧になる。
--     純粋な外部sales-viewerは DELETE（guard対象外）。棚卸等を兼ねる外部ユーザーは
--     'sales' だけ外す（本部操作時のみ。加盟オーナー操作なら本部依頼を促して中断）。
-- =====================================================================

create or replace function public.franchise_add_member(p_email text, p_store_id bigint)
returns text
language plpgsql
security definer
set search_path to 'public'
as $function$
declare v_email text := lower(trim(p_email));
begin
  if not public.can_view_store(p_store_id) then
    raise exception '権限がありません（この店舗を管理できません）';
  end if;
  if v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then
    raise exception 'メールアドレスの形式が正しくありません';
  end if;

  -- 旧名簿(app_users)。後方互換のため維持（新規は viewer）。
  insert into public.app_users(email, role) values (v_email, 'viewer')
    on conflict (email) do nothing;

  -- 店舗割当（未割当＝全店閲覧を絶対に作らないよう必ずセットで作る）。
  insert into public.app_user_stores(email, store_id) values (v_email, p_store_id)
    on conflict do nothing;

  -- ★ログインの入口(users)にも登録する。新規は外部viewer：
  --   is_internal=false → トリガ sync_user_role が role='staff'（アプリ上 viewer）に設定。
  --   segments に 'sales' を入れないと売上アプリのゲートを通れない。
  insert into public.users(email, display_name, is_active, is_internal, scan_only, segments)
    values (v_email, split_part(v_email, '@', 1), true, false, false, array['sales']::text[])
  on conflict (email) do nothing;

  -- 既存ユーザーで 'sales' を持たない場合は、本部(can_grant)操作時だけ付与する。
  --   （保護列変更は guard が本部以外を弾くため、本部操作に限定して安全に追記する。）
  if public.is_avend_granter() then
    update public.users
       set is_active = true,
           segments  = (select array(select distinct x
                                      from unnest(segments || array['sales']::text[]) x
                                      where x is not null and x <> ''))
     where email = v_email
       and not ('sales' = any(segments));
  end if;

  return v_email;
end
$function$;


create or replace function public.franchise_remove_member(p_email text, p_store_id bigint)
returns text
language plpgsql
security definer
set search_path to 'public'
as $function$
declare v_email text := lower(trim(p_email));
        v_role text;
        v_cnt int;
        v_internal boolean;
        v_scan boolean;
        v_segs text[];
begin
  if not public.can_view_store(p_store_id) then
    raise exception '権限がありません（この店舗を管理できません）';
  end if;

  delete from public.app_user_stores where email = v_email and store_id = p_store_id;
  select count(*) into v_cnt from public.app_user_stores where email = v_email;

  if v_cnt = 0 then
    -- 旧名簿(app_users)は従来どおり viewer を掃除。
    select role into v_role from public.app_users where email = v_email;
    if v_role = 'viewer' then
      delete from public.app_users where email = v_email;
    end if;

    -- ★users 側も掃除しないと「割当ゼロ＝本部扱い＝全店閲覧」になる。
    --   対象は外部ユーザー(is_internal=false)のみ（社内は別管理なので触らない）。
    select is_internal, scan_only, coalesce(segments, '{}')
      into v_internal, v_scan, v_segs
      from public.users where email = v_email;
    if found and v_internal = false then
      if v_scan = false and v_segs <@ array['sales']::text[] then
        -- 純粋な外部sales-viewer → 完全に取り消す（DELETE は guard 対象外）。
        delete from public.users where email = v_email;
      else
        -- 棚卸など他アクセスを兼ねる外部ユーザー → 'sales' だけ外して本部化を防ぐ。
        if public.is_avend_granter() then
          update public.users
             set segments = array_remove(coalesce(segments, '{}'), 'sales')
           where email = v_email;
        else
          raise exception 'この方は他システムのアカウントも兼ねているため、解除は本部にご依頼ください';
        end if;
      end if;
    end if;
  end if;

  return v_email;
end
$function$;
