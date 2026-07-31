-- =====================================================================
--  重複した店舗レコードを1つにまとめる関数 merge_store()
-- =====================================================================
--
-- 【これは何？】
--   同じ店なのに、システムごとの店名の違い（「NOTIME天王台店」と
--   「NOTIME天王台」、スペースの有無 など）で stores に別々の行として
--   登録されてしまうことがある。この関数は、重複した店(p_dupe)の売上・
--   来店・POS接続・実行ログを 正本(p_canonical) へ付け替えて、重複行を消す。
--
--   付け替え時に一意キーがぶつかる行（同じ伝票・同じ営業日の来店など）は
--   「正本側に既にある＝同じデータ」なので、重複側を捨てる。
--
-- 【使い方】
--   ・この関数を作る（このSQLを SQL Editor で Run）。
--   ・実際のまとめ込みは Actions「店舗の重複をまとめる」から自動実行する
--     （tools/dedupe_stores.py が、正規化キーで重複を見つけて呼ぶ）。
--
--   ※ service_role（ETL）だけが実行できる。画面(anon)からは呼べない。
-- =====================================================================

create or replace function public.merge_store(p_dupe bigint, p_canonical bigint)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_dupe is null or p_canonical is null or p_dupe = p_canonical then
    return;
  end if;

  -- 売上：自然キー(store_id,pos_name,tx_id,line_no)がぶつからない行だけ付け替え、残りは捨てる
  update public.sales s set store_id = p_canonical
   where s.store_id = p_dupe
     and not exists (
       select 1 from public.sales c
        where c.store_id = p_canonical and c.pos_name = s.pos_name
          and c.tx_id = s.tx_id and c.line_no = s.line_no);
  delete from public.sales where store_id = p_dupe;

  -- 来店：(business_date,store_id,source)
  update public.visits v set store_id = p_canonical
   where v.store_id = p_dupe
     and not exists (
       select 1 from public.visits c
        where c.store_id = p_canonical and c.business_date = v.business_date
          and c.source = v.source);
  delete from public.visits where store_id = p_dupe;

  -- POS接続：(store_id,pos_type,pos_name)
  update public.store_pos p set store_id = p_canonical
   where p.store_id = p_dupe
     and not exists (
       select 1 from public.store_pos c
        where c.store_id = p_canonical and c.pos_type = p.pos_type
          and c.pos_name = p.pos_name);
  delete from public.store_pos where store_id = p_dupe;

  -- 実行ログ：成功行の部分ユニーク(source,business_date,store)を避けて付け替え
  update public.ingest_log l set store_id = p_canonical
   where l.store_id = p_dupe
     and ( l.status <> 'success'
        or not exists (
             select 1 from public.ingest_log c
              where c.store_id = p_canonical and c.source = l.source
                and c.business_date = l.business_date and c.status = 'success'));
  delete from public.ingest_log where store_id = p_dupe;

  -- 店マスタの項目を、正本に足りない分だけ寄せる
  update public.stores can set
      digitel_slug   = coalesce(can.digitel_slug, dup.digitel_slug),
      cashier_label  = coalesce(can.cashier_label, dup.cashier_label),
      open_date      = coalesce(can.open_date, dup.open_date),
      has_entry_data = can.has_entry_data or dup.has_entry_data,
      kpi_visitors   = can.kpi_visitors and dup.kpi_visitors,
      -- 区分：どちらかがFCならFC（直営は4店だけの特別扱い）。両方が直営のときだけ直営。
      ownership      = case when can.ownership = '直営' and dup.ownership = '直営'
                           then '直営' else 'FC' end
    from public.stores dup
   where can.id = p_canonical and dup.id = p_dupe;

  delete from public.stores where id = p_dupe;
end;
$$;

revoke all on function public.merge_store(bigint, bigint) from public;
revoke all on function public.merge_store(bigint, bigint) from anon;
revoke all on function public.merge_store(bigint, bigint) from authenticated;
grant execute on function public.merge_store(bigint, bigint) to service_role;
