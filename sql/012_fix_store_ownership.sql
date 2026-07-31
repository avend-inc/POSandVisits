-- =====================================================================
--  店舗の区分（直営/FC）の直しと、店舗管理から保存できるようにする修正
-- =====================================================================
--
-- 【これは何？】
--   ① デジテールの自動発見で新しく増えた店（浜松・天王台・高松など）が、
--      stores.ownership の既定「直営」のまま入り、店舗比較の「直営店」に
--      混ざってしまっていたのを直す。
--      → 直営はこの4店だけ（山形・いわき・福井・下北沢）。それ以外はFC。
--   ② 店舗管理（admin.html）で区分などを保存しても元に戻る問題を直す。
--      原因は stores テーブルの「更新して良い」ルール（RLSポリシー）が
--      無い／効いていないこと。ここで管理者・編集者が更新できるように作る。
--
-- 【使い方】
--   Supabase 管理画面 → SQL Editor にこの中身を貼って [Run]。
--   （何度実行しても安全）
-- =====================================================================

-- ① 区分の直し ------------------------------------------------------
-- 直営はこの4店だけ、と明示する（既定に頼らない）。
update public.stores set ownership = '直営'
 where code in ('yamagata', 'iwaki', 'fukui', 'shimokitazawa');

-- 上の4店以外で「直営」になっているものはFCへ直す
-- （デジテールで自動追加された店などが既定の「直営」で入っているのを直す）。
update public.stores set ownership = 'FC'
 where ownership = '直営'
   and code not in ('yamagata', 'iwaki', 'fukui', 'shimokitazawa');

-- ② 店舗管理から保存できるようにする（更新ポリシー） ----------------
alter table public.stores enable row level security;

-- 閲覧：社内（@avend.co.jp）ログイン
drop policy if exists "stores_read" on public.stores;
create policy "stores_read" on public.stores
  for select to authenticated
  using ( public.current_app_email() like '%@avend.co.jp' );

-- 追加・変更・削除：編集者／管理者
drop policy if exists "stores_admin_write" on public.stores;
create policy "stores_admin_write" on public.stores
  for all to authenticated
  using ( public.current_app_role() in ('editor', 'admin') )
  with check ( public.current_app_role() in ('editor', 'admin') );

-- 確認（任意）
-- select id, name, code, ownership from public.stores order by ownership, id;
-- select email, role from public.app_users order by role;
