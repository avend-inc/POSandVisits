-- =====================================================================
--  sql/025_meta_campaign_stores.sql
--  Meta広告のキャンペーン → 店舗 の割り当てを、画面から直せるようにする
-- =====================================================================
--
-- 【なぜ別テーブルにするのか】
--   meta_insights_daily.destination_id（店舗）は avend-meta-ads の取り込みが
--   毎回そのまま書き込んでいる。つまり画面からその列を直接書き換えても、
--   次の取り込みで元に戻って消える。
--   そこで「上書き指定」だけを別テーブルに持ち、ダッシュボードを作るときに
--   取り込み済みの値の上からかぶせる。取り込み側は何も変えなくてよく、
--   何度取り直しても人が決めた割り当ては残る。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
--   前提: sql/019（is_hq）を実行済み。
-- =====================================================================

-- --- 割り当ての上書き表 -----------------------------------------------
--   1行 = 1キャンペーン。destination_id を null にすると「割り当てを外す」。
create table if not exists public.meta_campaign_stores (
  campaign_id    text primary key,
  destination_id uuid,                       -- 在庫アプリの destinations.id（店舗）
  campaign_name  text,                       -- 後から見て分かるように控えておくだけ
  note           text,
  updated_by     text,
  updated_at     timestamptz not null default now()
);

alter table public.meta_campaign_stores enable row level security;

-- 読み取りはAVENDメンバーのみ。書き込みポリシーは作らない
-- （＝画面から直接は書けない。下のRPC経由だけにする）。
drop policy if exists "meta_campaign_stores_read_hq" on public.meta_campaign_stores;
create policy "meta_campaign_stores_read_hq" on public.meta_campaign_stores
  for select to authenticated using (public.is_hq());

-- --- 割り当てる／外す --------------------------------------------------
--   p_destination_id を null にすると割り当てを外す（行は残して履歴にする）。
create or replace function public.meta_campaign_store_set(
  p_campaign_id    text,
  p_destination_id uuid,
  p_campaign_name  text default null)
returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.is_hq() then
    raise exception 'AVENDメンバーのみが変更できます';
  end if;
  if p_campaign_id is null or btrim(p_campaign_id) = '' then
    raise exception 'campaign_id が空です';
  end if;

  insert into public.meta_campaign_stores as m
        (campaign_id, destination_id, campaign_name, updated_by, updated_at)
  values (p_campaign_id, p_destination_id, p_campaign_name,
          public.current_app_email(), now())
  on conflict (campaign_id) do update
     set destination_id = excluded.destination_id,
         campaign_name  = coalesce(excluded.campaign_name, m.campaign_name),
         updated_by     = excluded.updated_by,
         updated_at     = now();
end $$;

revoke all on function public.meta_campaign_store_set(text, uuid, text) from public;
grant execute on function public.meta_campaign_store_set(text, uuid, text) to authenticated;

-- --- 割り当て先に選べる店舗の一覧 --------------------------------------
--   destinations（在庫アプリの納品先＝店舗）は在庫アプリ側のRLSが効いていて
--   ダッシュボードからは読めないことがあるので、AVENDメンバー向けに
--   id と名前だけを返す入り口を用意する。
create or replace function public.meta_destination_list()
returns table(id uuid, name text)
language sql stable security definer set search_path = public as $$
  select d.id, d.name
    from public.destinations d
   where public.is_hq()
   order by d.name
$$;

revoke all on function public.meta_destination_list() from public;
grant execute on function public.meta_destination_list() to authenticated;

-- 確認（任意）
--   select * from public.meta_campaign_stores order by updated_at desc;
--   select count(*) from public.meta_destination_list();
