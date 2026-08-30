-- =====================================================================
--  sql/038_lock_down_franchise.sql
--  加盟オーナー（社外）に「自店のページ以外」を一切見せない
--  実行日: 2026-08-30 / 実行済み（Supabase MCP）
-- =====================================================================
--
-- 【なぜ要ったか】
--   加盟オーナーへの店舗ページ共有を実運用に載せる前に、
--   「思いついた表を確認する」のではなく **DBの全オブジェクトを機械的に列挙して
--   1つずつ加盟オーナーの権限で叩く** 総当たり監査をしたところ、
--   在庫アプリ側の80テーブルが丸ごと読めることが分かった。
--   （請求書・オーナー・料率・ロイヤリティ・PL・事業計画・仕入原価・物件 など）
--
--   売上アプリ側（sql/019）は店舗単位で閉じてあったが、同じSupabaseに同居する
--   在庫アプリのテーブルは「社内の人しかログインしない」前提で作られており、
--   RLS無効か using(true) のままだった。加盟オーナーは同じ anon キーと
--   自分のJWTでPostgRESTを直接叩けるので、画面を隠しても意味がない。
--
-- 【さらに深刻だったもの】
--   未ログイン(anon)でも7テーブルが読めた（RLS無効＋anonにGRANT）。
--   app_settings もその1つで、**Slack の Webhook URL が2本入っていた**。
--   anonキーは公開JSに埋まっているので、事実上インターネットに露出していた。
--
-- 【入れたもの】
--   ① anon から public の全テーブル/ビューの権限を剥奪（既定権限も）
--   ② is_avend_internal() … 社内(is_internal)かつ有効かつ棚卸専用でない
--   ③ 在庫アプリの95テーブルに **restrictive** ポリシー zz_internal_guard。
--      restrictive は AND 合成なので、既存の using(true) を消さずに確実に締まる。
--      （permissive で足すと OR になって素通りする。最初これを踏んだ）
--   ④ stores / pos_store_links / destinations は棚卸スキャンで社外も使うため、
--      「社内 or 棚卸専用 or 自分の割り当て店」だけに絞る
--   ⑤ storage の auto-mail-files バケットは条件が bucket_id だけで、
--      ログインさえすれば誰でも読み書き削除できた → 社内のみに
--
-- 【除外したもの（社外が正当に使う）】
--   dash_*（売上ダッシュボード。既に店舗単位）/ scan_*（棚卸スキャン）/
--   users・app_users・app_user_stores・ai_usage（自分の行だけ）/
--   中野さん側で既に閉じているもの
--
-- 【実測（総当たり・138オブジェクト）】
--                     修正前            修正後
--   加盟オーナー      80が全件見える  → 遮断127 / 自分の分だけ10 / 全件0
--   棚卸だけの人      （同様に露出）  → 棚卸に必要なものだけ
--   社内(管理者)      全部見える      → 全部見える（変化なし＝壊していない）
--   未ログイン(anon)  7テーブル読めた → 0
--
--   書き込みも実測: 設定の書換・発送先の追加・請求書の全削除・売上の改ざん
--   すべて遮断されることを確認。
--   RPC も実測: bizplan_store_sales / staffed_visits_list / franchise_list_members /
--   meta_destination_list はいずれも加盟オーナーには0件（本部は取得できる）。
--
-- 【戻し方】
--   ・特定の表だけ緩める:
--       drop policy zz_internal_guard on public.<表名>;
--   ・全部戻す:
--       do $$ declare r record; begin
--         for r in select tablename from pg_policies
--                  where schemaname='public' and policyname='zz_internal_guard'
--         loop execute format('drop policy zz_internal_guard on public.%I', r.tablename); end loop;
--       end $$;
--   ・anon の権限は意図的に外したまま（両アプリともログイン後にしかDBを読まない）
-- =====================================================================

-- --- ① 未ログイン(anon)を全面的に締め出す -----------------------------
do $$
declare r record;
begin
  for r in select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
           where n.nspname='public' and c.relkind in ('r','v','m','p')
  loop execute format('revoke all on public.%I from anon', r.relname); end loop;
end $$;
alter default privileges in schema public revoke all on tables from anon;

-- --- ② 社内判定 -------------------------------------------------------
create or replace function public.is_avend_internal() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.users u
    where lower(u.email) = lower(coalesce(
            nullif(current_setting('request.jwt.claims', true)::json->>'email',''), ''))
      and u.is_active
      and coalesce(u.is_internal, false)
      and coalesce(u.scan_only, false) = false
  )
$$;
revoke all on function public.is_avend_internal() from public, anon;
grant execute on function public.is_avend_internal() to authenticated, service_role;
grant execute on function public.is_hq() to authenticated;   -- Edge Function から呼ぶ

-- --- ③ 在庫アプリのテーブルを社内のみに（restrictive＝AND合成） --------
do $$
declare r record;
  keep text[] := array[
    'dash_daily','dash_store','dash_category','dash_category_price','dash_benchmark','dash_bundle',
    'scan_counts','scan_units','scan_sessions','scan_racks','scan_tag_codes',
    'scan_counts_archive','scan_sessions_archive','scan_units_archive',
    'destinations','pos_store_links',
    'users','app_users','app_user_stores','ai_usage','stores',
    'sales','visits','footfall','ingest_log','nakano_inventory','mgmt_report','plan_data',
    'ai_conversations','line_accounts','line_broadcasts','line_daily','line_sources',
    'ig_accounts','ig_daily','ig_demographics','ig_sync_runs','meta_campaign_stores',
    'bukken_teacher_stores','bukken_teacher_bench','store_pos'
  ];
begin
  for r in select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
           where n.nspname='public' and c.relkind='r'
             and has_table_privilege('authenticated', c.oid,'SELECT')
             and not (c.relname = any(keep))
  loop
    execute format('alter table public.%I enable row level security', r.relname);
    execute format('drop policy if exists zz_internal_only on public.%I', r.relname);
    execute format('create policy zz_internal_only on public.%I for all to authenticated '
                   'using (public.is_avend_internal()) with check (public.is_avend_internal())',
                   r.relname);
    execute format('drop policy if exists zz_internal_guard on public.%I', r.relname);
    execute format('create policy zz_internal_guard on public.%I as restrictive to authenticated '
                   'using (public.is_avend_internal()) with check (public.is_avend_internal())',
                   r.relname);
  end loop;
end $$;

-- --- ④ 店舗名の一覧も自分に関係する分だけに ---------------------------
drop policy if exists zz_store_scope on public.stores;
create policy zz_store_scope on public.stores as restrictive to authenticated
  using (
    public.is_avend_internal()
    or exists (select 1 from public.users u
               where lower(u.email)=lower(auth.jwt()->>'email')
                 and u.is_active and coalesce(u.scan_only,false))
    or public.can_view_store(id)
  );

drop policy if exists zz_link_scope on public.pos_store_links;
create policy zz_link_scope on public.pos_store_links as restrictive to authenticated
  using (
    public.is_avend_internal()
    or exists (select 1 from public.users u
               where lower(u.email)=lower(auth.jwt()->>'email')
                 and u.is_active and coalesce(u.scan_only,false))
    or exists (select 1 from public.app_user_stores aus
               where lower(aus.email)=lower(auth.jwt()->>'email')
                 and aus.store_id = pos_store_links.pos_store_id)
  );

alter table public.destinations enable row level security;
drop policy if exists zz_dest_read on public.destinations;
create policy zz_dest_read on public.destinations for all to authenticated
  using (public.is_avend_internal() or public.scan_can_see_destination(id))
  with check (public.is_avend_internal());

-- --- ⑤ 自動メールの添付バケット --------------------------------------
drop policy if exists "auto mail files all" on storage.objects;
create policy "auto mail files internal" on storage.objects for all to authenticated
  using (bucket_id = 'auto-mail-files' and public.is_avend_internal())
  with check (bucket_id = 'auto-mail-files' and public.is_avend_internal());

notify pgrst, 'reload schema';
