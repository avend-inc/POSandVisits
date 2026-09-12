-- =====================================================================
--  sql/045_dash_category_trend.sql
--  推移タブ（運営）の「販売カテゴリ比率」を軽く出すための集計RPC
--
--  dash_category（date, store_id, category, amount, qty）を、期間粒度で
--  サーバ側に GROUP BY してから返す。全店×長期間を素の行で引くと重いので、
--  期間×カテゴリに畳んでから渡す（週=月曜始まり／月=月初／日=その日）。
--
--  ・SECURITY DEFINER。dash_category の RLS と同じ可視範囲を関数内で再現する
--    （本部は全店、割り当てのある人は自店のみ）。DEFINER で RLS を素通りするため、
--    ここで絞らないと他店が見えてしまう。
--  ・p_store_ids が NULL なら（可視範囲内で）全店。配列を渡せばその店だけ。
--  ・week は date_trunc('week') ＝ ISO(月曜)始まり。画面側の月曜丸めと一致する。
-- =====================================================================
create or replace function public.dash_category_trend(
    p_since date, p_gran text, p_store_ids bigint[] default null)
  returns table(period date, category text, qty numeric, amount numeric)
  language sql
  stable security definer
  set search_path to 'public'
as $$
  select
    case p_gran
      when 'month' then date_trunc('month', c.date)::date
      when 'week'  then date_trunc('week',  c.date)::date
      else c.date
    end as period,
    c.category,
    sum(c.qty)    as qty,
    sum(c.amount) as amount
  from public.dash_category c
  where c.date >= p_since
    and (p_store_ids is null or c.store_id = any(p_store_ids))
    and (
      public.is_hq()
      or c.store_id in (
        select aus.store_id from public.app_user_stores aus
         where aus.email = public.current_app_email()
      )
    )
  group by 1, 2
$$;

grant execute on function public.dash_category_trend(date, text, bigint[]) to authenticated;
