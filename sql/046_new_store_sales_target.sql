-- =====================================================================
--  sql/046_new_store_sales_target.sql
--  新店舗（福井より後の直営店）の「税抜売上 目標」を新店舗モデルから算出するRPC
--
--  事業計画では、福井より後の新店舗は per-店プランを作らず、
--  「新店舗モデル(bizplan_cells 'new_store_model' の 売上・カレンダー月1〜12)
--    ＋ オープン景気売上(開業1〜3ヶ月目 = bizplan_monthly 'd5' の param)」
--  で目標を置いている（在庫アプリ lib/bizplanCore.ts と同じ考え方）。
--
--  new_store_sales_target(p_open_ym, p_ym):
--    p_open_ym … その店の開業年月 'YYYY-MM'
--    p_ym      … 目標を知りたい年月 'YYYY-MM'
--    返り値    … 税抜売上の目標（開業前なら null）
--      base = モデル売上[ p_ym のカレンダー月 ]
--      k    = p_open_ym から p_ym までの経過月（0=開業月, 1, 2, …）
--      景気 = k=0:開業月 / k=1:2ヶ月目 / k=2:3ヶ月目 / それ以降:0
--      目標 = base + 景気
--  ・SECURITY DEFINER＋is_hq()。既存の bizplan RPC と同じ可視範囲。
-- =====================================================================
create or replace function public.new_store_sales_target(p_open_ym text, p_ym text)
  returns numeric
  language plpgsql
  stable security definer
  set search_path to 'public'
as $$
declare
  v_base   numeric;
  v_k      int;
  v_boost  numeric := 0;
  v_open   date;
  v_tgt    date;
begin
  if not public.is_hq() then return null; end if;
  if p_open_ym is null or p_ym is null then return null; end if;
  v_open := to_date(p_open_ym || '-01', 'YYYY-MM-DD');
  v_tgt  := to_date(p_ym      || '-01', 'YYYY-MM-DD');
  if v_tgt < v_open then return null; end if;   -- 開業前

  -- モデル売上（カレンダー月）。month 列は 1〜12
  select c.amount into v_base
    from public.bizplan_cells c
   where c.plan_key = 'new_store_model' and c.item = '売上'
     and (c.month)::int = extract(month from v_tgt)::int
   limit 1;
  if v_base is null then return null; end if;

  v_k := (extract(year from v_tgt)::int - extract(year from v_open)::int) * 12
       + (extract(month from v_tgt)::int - extract(month from v_open)::int);

  select coalesce(m.amount,0) into v_boost from public.bizplan_monthly m
   where m.plan_key='d5' and m.ym='param'
     and m.item = case v_k when 0 then 'オープン景気売上'
                           when 1 then 'オープン景気売上2ヶ月目'
                           when 2 then 'オープン景気売上3ヶ月目'
                           else '__none__' end
   limit 1;

  return v_base + coalesce(v_boost,0);
end;
$$;

grant execute on function public.new_store_sales_target(text, text) to authenticated;
