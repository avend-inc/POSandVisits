-- =====================================================================
--  加盟店セルフサービス：店舗オーナーが「自分の店」に他アカウントを追加/削除
-- =====================================================================
--
-- 【これは何？】
--   店舗ページ（加盟店モード）から、その店のオーナーが自分の店だけに
--   閲覧アカウント（viewer）を追加・削除・一覧できるようにする RPC。
--   直接テーブルを触らせず、SECURITY DEFINER 関数で「自分の店だけ」を
--   サーバ側で強制する（can_view_store）。
--
-- 【使い方】
--   Supabase 管理画面 → SQL Editor にこの中身を貼って [Run]。sql/019 実行済みが前提。
--
-- 【安全性の要】
--   ・加盟店が「メールだけ追加して店舗未割当」にすると is_hq()=true（全店閲覧）に
--     なってしまう。だから追加は必ず「app_users登録 ＋ その店への割当」をセットで行う。
--   ・削除で viewer の割当が0になると同じく全店閲覧になるため、その場合はログイン自体を取り消す。
--   ・admin/editor は is_hq() で常に本部扱い＝店舗を割り当てても降格しない（019で担保）。
-- =====================================================================

-- 店舗のメンバー一覧（その店を閲覧できる人だけが取得できる）
create or replace function public.franchise_list_members(p_store_id bigint)
returns table(email text, name text, role text)
language sql stable security definer set search_path = public as $$
  select au.email, au.name, au.role
  from public.app_user_stores aus
  join public.app_users au on au.email = aus.email
  where aus.store_id = p_store_id
    and public.can_view_store(p_store_id)
  order by au.email
$$;

-- メンバー追加（自分の店だけ・viewerとして・app_users登録＋店舗割当をセットで）
create or replace function public.franchise_add_member(p_email text, p_store_id bigint)
returns text language plpgsql security definer set search_path = public as $$
declare v_email text := lower(trim(p_email));
begin
  if not public.can_view_store(p_store_id) then
    raise exception '権限がありません（この店舗を管理できません）';
  end if;
  if v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then
    raise exception 'メールアドレスの形式が正しくありません';
  end if;
  -- 既存ユーザーのロールは変更しない（管理者などを降格させない）。新規は viewer。
  insert into public.app_users(email, role) values (v_email, 'viewer')
    on conflict (email) do nothing;
  -- 必ず店舗割当をセットで作る（未割当＝全店閲覧、を絶対に作らない）
  insert into public.app_user_stores(email, store_id) values (v_email, p_store_id)
    on conflict do nothing;
  return v_email;
end $$;

-- メンバー削除（自分の店だけ）。viewer の割当が0になったらログインごと取り消す（全店閲覧化を防ぐ）。
create or replace function public.franchise_remove_member(p_email text, p_store_id bigint)
returns text language plpgsql security definer set search_path = public as $$
declare v_email text := lower(trim(p_email)); v_role text; v_cnt int;
begin
  if not public.can_view_store(p_store_id) then
    raise exception '権限がありません（この店舗を管理できません）';
  end if;
  delete from public.app_user_stores where email = v_email and store_id = p_store_id;
  select role into v_role from public.app_users where email = v_email;
  select count(*) into v_cnt from public.app_user_stores where email = v_email;
  if v_role = 'viewer' and v_cnt = 0 then
    delete from public.app_users where email = v_email;   -- 全店閲覧化を防ぐため完全に取り消す
  end if;
  return v_email;
end $$;

grant execute on function public.franchise_list_members(bigint)   to authenticated;
grant execute on function public.franchise_add_member(text,bigint) to authenticated;
grant execute on function public.franchise_remove_member(text,bigint) to authenticated;

-- 確認（任意）：
--   select * from public.franchise_list_members(24);   -- 24=店舗id
