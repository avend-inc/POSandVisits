-- ============================================================
--  sql/008_ai_conversations.sql
--  分析AIの「質問スレッド」を保存する（質問者・タイトル・やり取り全文）。
--  ・1スレッド = 1レコード。messages に [{role, content, by, at}, ...] を蓄積。
--  ・登録メンバー(app_users)なら全員が閲覧・追加・更新できる（社内ナレッジ共有）。
--  ・端末間リアルタイム反映のため supabase_realtime に追加。
--  前提: sql/005 の current_app_role() が作成済みであること。
-- ============================================================
create extension if not exists pgcrypto;

create table if not exists public.ai_conversations (
  id          uuid primary key default gen_random_uuid(),
  title       text not null default '(無題)',
  created_by  text not null default '',      -- 質問者(メールアドレス)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  messages    jsonb not null default '[]'::jsonb   -- [{role:'user'|'assistant', content, by, at}]
);

create index if not exists ai_conversations_updated_idx on public.ai_conversations (updated_at desc);

alter table public.ai_conversations enable row level security;

-- 閲覧：登録メンバーは全スレッドを見られる
drop policy if exists ai_conv_select on public.ai_conversations;
create policy ai_conv_select on public.ai_conversations
  for select using (current_app_role() is not null);

-- 追加：登録メンバーなら新規スレッドを作れる
drop policy if exists ai_conv_insert on public.ai_conversations;
create policy ai_conv_insert on public.ai_conversations
  for insert with check (current_app_role() is not null);

-- 更新：登録メンバーなら追記（追加質問）できる
drop policy if exists ai_conv_update on public.ai_conversations;
create policy ai_conv_update on public.ai_conversations
  for update using (current_app_role() is not null)
  with check (current_app_role() is not null);

-- 削除：管理者のみ
drop policy if exists ai_conv_delete on public.ai_conversations;
create policy ai_conv_delete on public.ai_conversations
  for delete using (current_app_role() = 'admin');

-- リアルタイム反映（端末間同期）。すでに追加済みならスキップ。
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname='supabase_realtime' and schemaname='public' and tablename='ai_conversations'
  ) then
    alter publication supabase_realtime add table public.ai_conversations;
  end if;
end $$;
