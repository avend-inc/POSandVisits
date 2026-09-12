-- =====================================================================
--  sql/044_bizplan_store_pl.sql
--  月次予実（運営タブ）の「予算」を事業計画から引くためのRPC
--
--  既存 bizplan_store_sales(p_ym) は「売上」1項目だけを返す。月次予実では
--  原価・家賃・人件費・広告費…も要るので、直営店(kind='store')の
--  P&L項目を丸ごと返す版を用意する。
--
--  ・SECURITY DEFINER＋is_hq() 判定は bizplan_store_sales と同じ
--    （本部/管理・編集、割り当ての無い閲覧者だけが読める。加盟オーナーには出ない）。
--  ・返すのは金額系の月次行だけ（ym が YYYY-MM のもの）。
--    param/ratio 等の特殊行・人数(人数:社員/アルバイト)は返さない。
--  ・pos_store_name で直営店（下北沢/山形/いわき/福井）に紐づく。
-- =====================================================================
create or replace function public.bizplan_store_pl(p_ym text)
  returns table(pos_store_name text, item text, amount numeric)
  language sql
  stable security definer
  set search_path to 'public'
as $$
  select p.pos_store_name, m.item, m.amount
    from public.bizplan_plans   p
    join public.bizplan_monthly m on m.plan_key = p.plan_key
   where public.is_hq()
     and p.kind = 'store'
     and p.pos_store_name is not null
     and m.ym = p_ym
     and m.ym ~ '^[0-9]{4}-[0-9]{2}$'
     and m.item <> all (array['人数:社員','人数:アルバイト'])
$$;

grant execute on function public.bizplan_store_pl(text) to authenticated;
