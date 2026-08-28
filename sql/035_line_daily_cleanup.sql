-- =====================================================================
--  sql/035_line_daily_cleanup.sql
--  LINE日次（line_daily）から「開設前の0埋め」を落とす
--  実行日: 2026-08-28 / 実行済み（Supabase MCP）
-- =====================================================================
--
-- 【何が起きていたか】
--   デジテールの友だちCSV（/{店舗}/kpi/members/friends/download）は、
--   from を指定しても 2019-01-01 から1日1行を返し、アカウントが動き出す前の
--   日も 0 で埋めてくる。初回の一括取り込みで 61,512行 入ったが、
--   実際に数字があるのは 5,967行だけだった（残りは開設前の0と、
--   データが1日も無い NOTIME小倉魚町店の 2,796行）。
--   そのままだと友だち数のグラフが何年も0のまま伸びて読めない。
--
-- 【戻し方】
--   消す前に全行を _line_daily_backup_20260828 に退避してある（61,512行）。
--     insert into public.line_daily select * from public._line_daily_backup_20260828
--       on conflict (date, account_id) do nothing;
--   取り込み直しでも戻せる（python -m etl.line_fetch --all）。
--   ただし取り込み側も同じ条件で落とすようにしたので（etl/line_rows.py の
--   _drop_before_open）、取り直しても0埋めは入らない。
--
-- 【再発防止】
--   ここはあくまで入ってしまった分の後始末。今後の取り込みは
--   etl/line_rows.py 側で落とす。
-- =====================================================================

-- ① 退避（実行済み。消えていたら作り直せるよう残しておく）
create table if not exists public._line_daily_backup_20260828 as
  select * from public.line_daily;

-- ② アカウントごとに「累積友だち数が初めて1以上になった日」より前を消す。
--    1日も数字が無いアカウントは全部消す（opened_at is null）。
with firsts as (
  select account_id,
         min(date) filter (where coalesce(followers, 0) > 0) as opened_at
    from public.line_daily
   group by account_id
)
delete from public.line_daily d
 using firsts f
 where d.account_id = f.account_id
   and (f.opened_at is null or d.date < f.opened_at);

-- 確認:
--   select count(*), min(date), max(date), count(distinct account_id)
--     from public.line_daily;
--   → 5,967行 / 2025-02-26 〜 2026-08-27 / 21アカウント（2026-08-28 実行時）
