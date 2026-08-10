-- =====================================================================
--  sql/021_staffed_visits.sql
--  「有人時間の来店数」を画面から手入力できるようにする（下北沢向け）
-- =====================================================================
--
-- 【背景】
--   下北沢は 1店舗＝2レジ運用。
--     ・SIPOS（EZレジ） … 無人営業時間
--     ・Airレジ         … 有人営業時間
--   来店（入店）計測は無人時間ぶんしか自動で取れないため、来店数・購入率が
--   実態より小さく出てしまう（これが kpi_visitors=false の理由）。
--   そこで「有人時間の来店数」を店舗ページから日別に手入力できるようにし、
--   無人（自動取得）＋有人（手入力）の合算をその店の来店数として使う。
--
-- 【入れ物】
--   新しいテーブルは作らない。既存の visits に source='staffed' で1日1行入れる。
--   visits の自然キーは (business_date, store_id, source) なので、
--   同じ日を二度入れても上書きになり、自動取得ぶん(source='digitel')とも衝突しない。
--
-- 【安全性】
--   visits は RLS 有効（sql/005）で画面から直接は触れない。
--   読み書きは下の SECURITY DEFINER 関数だけを通す。関数の中で
--   can_view_store()（sql/019）により「その店舗を見られる人だけ」を強制する。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
--   前提: 005（app_users/current_app_role）・019（can_view_store）を実行済み。
-- =====================================================================

-- --- 読み取り：期間内の「有人時間の来店数」を返す -----------------------
create or replace function public.staffed_visits_list(
  p_store_id bigint,
  p_from     date,
  p_to       date
) returns table(d date, visitors integer)
language sql stable security definer set search_path = public as $$
  select v.business_date, v.visitors
    from public.visits v
   where v.store_id = p_store_id
     and v.source = 'staffed'
     and v.business_date between p_from and p_to
     and public.can_view_store(p_store_id)
   order by v.business_date
$$;

-- --- 書き込み：1日ぶんを登録／更新／削除 -------------------------------
--   p_visitors が NULL … その日の入力を取り消す（行を消す）
--   0以上の整数        … その日の有人来店数として保存（既にあれば上書き）
create or replace function public.staffed_visits_upsert(
  p_store_id bigint,
  p_date     date,
  p_visitors integer
) returns integer
language plpgsql security definer set search_path = public as $$
declare v_today date := (now() at time zone 'Asia/Tokyo')::date;
begin
  if not public.can_view_store(p_store_id) then
    raise exception '権限がありません（この店舗を編集できません）';
  end if;
  if p_date is null then
    raise exception '日付が指定されていません';
  end if;
  if p_date > v_today then
    raise exception '未来の日付には入力できません（%）', p_date;
  end if;

  if p_visitors is null then
    delete from public.visits
     where store_id = p_store_id and business_date = p_date and source = 'staffed';
    return null;
  end if;

  if p_visitors < 0 then
    raise exception '来店数は0以上で入力してください';
  end if;

  insert into public.visits (business_date, store_id, source, visitors)
  values (p_date, p_store_id, 'staffed', p_visitors)
  on conflict (business_date, store_id, source)
  do update set visitors = excluded.visitors, ingested_at = now();

  return p_visitors;
end
$$;

-- 画面（ログイン済みユーザー）からだけ呼べるようにする。未ログイン(anon)は不可。
revoke all on function public.staffed_visits_list(bigint, date, date)     from public, anon;
revoke all on function public.staffed_visits_upsert(bigint, date, integer) from public, anon;
grant execute on function public.staffed_visits_list(bigint, date, date)     to authenticated;
grant execute on function public.staffed_visits_upsert(bigint, date, integer) to authenticated;

-- 確認（任意）
--   select * from public.staffed_visits_list(
--     (select id from public.stores where name like '%下北沢%'),
--     date '2026-08-01', date '2026-08-31');
