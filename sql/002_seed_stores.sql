-- ============================================================
--  店舗マスタの初期データ
--  001_schema.sql のあとに、同じく SQL Editor で実行してください。
--  何度実行しても重複しません（同じ code は上書きされます）。
--
--  ※ ETL は、CSVに知らない店舗名が出てきたら自動でこの表に追加します。
--     ここで先に入れておくのは、店舗コードや開店日をきれいに保つためです。
-- ============================================================

insert into public.stores (code, name, digitel_slug, cashier_label, open_date, has_entry_data)
values
    ('yamagata',     '山形',   'notime_yamagata',     'NOTIME山形',   '2026-03-20', true),
    ('iwaki',        'いわき', 'notime_iwaki',        'NOTIMEいわき', '2026-04-25', true),
    ('fukui',        '福井',   'notime_fukui',        'NOTIME福井',   '2026-06-13', true),
    ('shimokitazawa','下北沢', 'notime_shimokitazawa', null,          '2026-01-01', false)
on conflict (code) do update set
    name           = excluded.name,
    digitel_slug   = excluded.digitel_slug,
    cashier_label  = excluded.cashier_label,
    open_date      = excluded.open_date,
    has_entry_data = excluded.has_entry_data;
