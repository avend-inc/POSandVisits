-- 一時的な自己テスト（適用後に削除するファイル）。
-- 本部役(sho=can_grant) と 加盟オーナー役(横地=店39割当・非本部) の両方で
-- franchise_add_member → 検証 → franchise_remove_member → 検証 を行い、
-- users への同期と、解除時の後始末（本部化の防止）を確認する。投げ捨てメールでnet-zero。
do $$
declare
  hq   text := 'sho.nakano@avend.co.jp';          -- 本部(can_grant=true)
  fran text := '09056596756@docomo.ne.jp';        -- 加盟オーナー(店39=SELFURUGI隠岐店 割当・非本部)
  e1   text := 'selftest.hq.deleteme@example.invalid';
  e2   text := 'selftest.fc.deleteme@example.invalid';
  sid  bigint := 39;
  n int;
begin
  -- ===== ① 本部役で 追加 =====
  perform set_config('request.jwt.claims', json_build_object('email', hq)::text, true);
  perform public.franchise_add_member(e1, sid);
  select count(*) into n from public.users where email=e1 and is_active and 'sales'=any(segments) and is_internal=false;
  if n <> 1 then raise exception '①users未作成: %', e1; end if;
  select count(*) into n from public.app_user_stores where email=e1 and store_id=sid;
  if n <> 1 then raise exception '①割当未作成: %', e1; end if;
  -- ===== ① 本部役で 解除 =====
  perform public.franchise_remove_member(e1, sid);
  select count(*) into n from public.users where email=e1;
  if n <> 0 then raise exception '①users未掃除: %', e1; end if;
  select count(*) into n from public.app_user_stores where email=e1;
  if n <> 0 then raise exception '①割当未掃除: %', e1; end if;
  raise notice '① 本部役 add/remove: OK';

  -- ===== ② 加盟オーナー役（非本部）で 追加 =====
  perform set_config('request.jwt.claims', json_build_object('email', fran)::text, true);
  perform public.franchise_add_member(e2, sid);
  select count(*) into n from public.users where email=e2 and is_active and 'sales'=any(segments) and is_internal=false;
  if n <> 1 then raise exception '②users未作成(非本部): %', e2; end if;
  select count(*) into n from public.app_user_stores where email=e2 and store_id=sid;
  if n <> 1 then raise exception '②割当未作成(非本部): %', e2; end if;
  -- ===== ② 加盟オーナー役で 解除（純粋な外部viewerなのでDELETEされる想定）=====
  perform public.franchise_remove_member(e2, sid);
  select count(*) into n from public.users where email=e2;
  if n <> 0 then raise exception '②users未掃除(非本部): %', e2; end if;
  raise notice '② 加盟オーナー役 add/remove: OK';

  raise notice '=== 自己テスト 全項目 OK ===';
end $$;
