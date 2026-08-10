-- =====================================================================
--  sql/022_bizplan_budget.sql
--  店舗ページの「売上予算」カードが、経営の事業計画の数字を読めるようにする
-- =====================================================================
--
-- 【背景】
--   事業計画（在庫アプリ側の「経営」タブ）は、同じ Supabase プロジェクトの
--     bizplan_plans   … 計画の台帳（plan_key / name / kind / pos_store_name）
--     bizplan_monthly … 月次のセル（plan_key / item / ym / amount）
--   に入っている。店舗ページの売上予算はここを正としたい。
--   計画の「売上」は POS実績（data.json の daily.ex＝税抜純売上）を取り込む
--   前提で作られているので、税抜どうしでそのまま比較できる。
--
-- 【なぜ関数を挟むか】
--   bizplan_* は RLS で「管理者(is_admin)だけ」に絞られている。ダッシュボードは
--   管理者以外（閲覧ロール）も開くので、そのままでは予算が読めない。
--   かといって表を直接開放すると、加盟店(FC)アカウントにも直営の事業計画が
--   見えてしまう。そこで SECURITY DEFINER の関数を1本だけ用意し、
--     ・返すのは「店舗計画の、その月の売上」だけ（原価も販管費も返さない）
--     ・AVENDメンバー（is_hq＝全店を見られる社内アカウント）にだけ返す。
--       加盟店(FC)は社外なので、直営の計画も他店の計画も一切返さない（空になる）
--   に絞る。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
--   前提: 在庫アプリ側の add_bizplan_company.sql（bizplan_*）と、
--         こちらの sql/019（is_hq）を実行済みであること。
-- =====================================================================

create or replace function public.bizplan_store_sales(p_ym text)
returns table(pos_store_name text, amount numeric)
language sql stable security definer set search_path = public as $$
  select p.pos_store_name, m.amount
    from public.bizplan_plans   p
    join public.bizplan_monthly m on m.plan_key = p.plan_key
   where public.is_hq()                    -- AVENDメンバーのみ。加盟店(社外)には何も返さない
     and p.kind = 'store'                  -- 店舗の計画だけ（本部・FC事業部などは対象外）
     and p.pos_store_name is not null
     and m.item = '売上'
     and m.ym = p_ym
$$;

revoke all on function public.bizplan_store_sales(text) from public, anon;
grant execute on function public.bizplan_store_sales(text) to authenticated;

-- 確認（任意）: AVENDメンバーのアカウントで実行すると、その月の店舗別 売上計画が並ぶ
--   select * from public.bizplan_store_sales('2026-08');
