-- =====================================================================
--  sql/033_ai_security.sql
--  分析AIの「見える範囲」と「使いすぎ」を締める
-- =====================================================================
--
-- 【① スレッドが全員に見えていた】
--   sql/008 では ai_conversations の閲覧条件が
--     using ( public.current_app_role() is not null )
--   つまり app_users に行がある人は全員、全スレッドを読めた。
--
--   sql/019 で加盟店オーナーを店舗単位に閉じたが、オーナーも app_users に
--   role='viewer' で登録される。結果、019 でせっかく閉じた店舗分離を、
--   AIのスレッド（全店データを分析した内容が本文に入っている）から
--   素通りで読めてしまう状態だった。
--
--   → 社内(@avend.co.jp)だけが読める形に直す。社内のナレッジ共有という
--     元の意図はそのまま（社内なら他人のスレッドも読める）。
--
-- 【② 回数の上限が無かった】
--   Edge Function "ask" にレート制限が無く、登録メンバーの誰か1人が
--   スクリプトを回すだけで Anthropic の課金を無制限に積めた。
--   1人1日あたりの回数を数える表を置く。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
--   前提: sql/005（current_app_role / current_app_email）、sql/008 を実行済み。
-- =====================================================================

-- --- ① ai_conversations：社内のみ ------------------------------------
--   ※ 加盟店オーナーは分析AIを使わない（売上アプリの自店ページのみ）。
drop policy if exists ai_conv_select on public.ai_conversations;
create policy ai_conv_select on public.ai_conversations
  for select to authenticated
  using ( public.current_app_email() like '%@avend.co.jp' );

drop policy if exists ai_conv_insert on public.ai_conversations;
create policy ai_conv_insert on public.ai_conversations
  for insert to authenticated
  with check ( public.current_app_email() like '%@avend.co.jp' );

drop policy if exists ai_conv_update on public.ai_conversations;
create policy ai_conv_update on public.ai_conversations
  for update to authenticated
  using ( public.current_app_email() like '%@avend.co.jp' )
  with check ( public.current_app_email() like '%@avend.co.jp' );

-- 削除は従来どおり管理者のみ（008 のまま。再掲して取り違えを防ぐ）
drop policy if exists ai_conv_delete on public.ai_conversations;
create policy ai_conv_delete on public.ai_conversations
  for delete to authenticated
  using ( public.current_app_role() = 'admin' );


-- --- ② 利用回数（1人1日） --------------------------------------------
create table if not exists public.ai_usage (
  email text not null,
  day   date not null,                     -- JSTの日付
  count integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (email, day)
);

alter table public.ai_usage enable row level security;

-- 自分の使用回数だけ見られる（画面に「残りN回」を出したくなったとき用）。
-- 書き込みポリシーは作らない＝ service_role（Edge Function）以外は増やせない。
drop policy if exists ai_usage_self_read on public.ai_usage;
create policy ai_usage_self_read on public.ai_usage
  for select to authenticated
  using ( email = public.current_app_email() );

-- 加算。同時に呼ばれても取りこぼさないよう upsert 1文で行う。
create or replace function public.bump_ai_usage(p_email text, p_day date)
returns integer
language sql
security definer
set search_path = public
as $$
  insert into public.ai_usage (email, day, count, updated_at)
  values (lower(p_email), p_day, 1, now())
  on conflict (email, day) do update
    set count = ai_usage.count + 1, updated_at = now()
  returning count;
$$;

-- 呼べるのは Edge Function（service_role）だけ。画面から回数を水増し/リセットできないようにする。
revoke all on function public.bump_ai_usage(text, date) from public;
revoke all on function public.bump_ai_usage(text, date) from anon;
revoke all on function public.bump_ai_usage(text, date) from authenticated;
grant execute on function public.bump_ai_usage(text, date) to service_role;

notify pgrst, 'reload schema';

-- 確認（任意）:
--   select * from public.ai_usage order by day desc, count desc limit 20;
