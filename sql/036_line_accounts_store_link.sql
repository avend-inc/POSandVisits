-- =====================================================================
--  sql/036_line_accounts_store_link.sql
--  LINEアカウント → POS店舗 の紐付け（22件）
--  実行日: 2026-08-28 / 実行済み（Supabase MCP）
-- =====================================================================
--
-- 【なぜ要るか】
--   マーケのLINEタブの「店舗」の絞り込みは line_accounts.store_id で効く。
--   初回の取り込み直後は22件すべて null で、選択肢が1つも出ず
--   全店合計しか見られない状態だった。
--
-- 【どう対応させたか】
--   デジテールの店名とPOS(stores)の店名は表記が違う。
--     セルフルギ赤羽店      → SELFURUGIGARAGE赤羽店
--     SELFURUGI GARAGE静岡店 → SELFURUGIGARAGE静岡店
--   カタカナ・スペース・GARAGE の有無をならして突き合わせ、
--   1つに絞れたものだけを入れてある（22件すべて確定した）。
--   同じ規則を etl/line_fetch.py の link_stores() にも入れたので、
--   店が増えても取り込みのたびに自動で埋まる。空のまま残ったものは
--   実行ログに店名が出る。
--
-- 【戻し方】
--   実行前は22件すべて store_id が null だった。
--     update public.line_accounts set store_id = null;
--   で元に戻る（このあと人が直した紐付けまで消えるので注意）。
-- =====================================================================

update public.line_accounts a set store_id = v.sid
from (values
  ('notime_fukui',3),('notime_hamamatsu',46),('notime_iwaki',2),('notime_kamagaya',18),
  ('notime_kokurauomachi',15),('notime_kumagaya',29),('notime_masakicho',40),
  ('notime_shimokitazawa',4),('notime_shizuoka',33),('notime_takamatsu',48),
  ('notime_tennodai',47),('notime_tokorozawa',55),('notime_waseda',24),('notime_yamagata',1),
  ('selffurugi_akabane',28),('selffurugi_isesaki',26),('selffurugi_kumagaya',30),
  ('selffurugi_machida',11),('selffurugi_shizuoka',34),('selffurugi_tokushimaokihama',27),
  ('selffurugi_utsunomiya',35),('selfurugi_kamagaya',38)
) as v(aid, sid)
where a.account_id = v.aid and a.store_id is distinct from v.sid;

-- 確認:
--   select a.account_id, a.name, s.name from public.line_accounts a
--     left join public.stores s on s.id = a.store_id order by a.store_id nulls first;
