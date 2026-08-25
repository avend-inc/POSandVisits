-- =====================================================================
--  sql/032_bukken_teacher_data.sql
--  出店判断の「教師データ」をテーブルに移す（公開ページへの直書きをやめる）
-- =====================================================================
--
-- 【なぜ必要か】
--   web/mise.html（既存店データ）と web/shoken.html（商圏調査）は、
--   店舗ごとの家賃・商圏人口・通行量・成否判定を、JavaScriptの定数として
--   HTMLファイルに直接書き込んでいた。競合店（ヒューマンイズデッド／Syndi）の
--   数字も入っている。
--
--   両ページにはログイン判定があるが、判定はブラウザ側でしか動かない。
--   データがファイルそのものに入っている以上、ページのソースを表示すれば
--   ログインせずに全部読める。しかも gh-pages で配信していた
--   （GitHub Pages はリポジトリが private でも公開配信される）。
--
--   ＝ 画面のログインでは守れない。データをDBに移し、RLSで閉じる。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
--   前提: sql/005（current_app_role / current_app_email）を実行済み。
-- =====================================================================

-- --- 既存店＋競合店の実績（mise.html が読む）--------------------------
create table if not exists public.bukken_teacher_stores (
  name    text primary key,
  ours    boolean not null default true,   -- 自社かどうか（false＝競合）
  pref    text,
  city    text,
  model   text,                            -- 地方 / 都心郊外 / 大型地方
  road    boolean,                         -- ロードサイドか
  success text,                            -- 成功 / 普通 / 失敗 / null(判定前)
  area    numeric,                         -- 坪
  rent    numeric,                         -- 月額家賃
  park    integer,                         -- 駐車台数
  p3 integer, p10 integer, p20 integer, p30 integer, p40 integer,  -- 各距離帯の20-34歳人口
  indep   numeric,                         -- 独立度＝10km÷40km
  traf    integer,                         -- 通行量（上下・24h・小型車）
  updated_at timestamptz not null default now()
);

-- --- 商圏調査の判定に使う基準店（shoken.html が読む）------------------
create table if not exists public.bukken_teacher_bench (
  n     text primary key,                  -- 表示用の短い店名
  g     text not null,                     -- road_success / urbanA / fail
  p3 integer, p10 integer, p20 integer, p30 integer, p40 integer,
  traf  integer,
  indep numeric,
  muni  text,
  updated_at timestamptz not null default now()
);

-- --- RLS：社内(@avend.co.jp)のログインユーザーだけ読める --------------
--   加盟店オーナーには見せない（家賃・競合の数字が入っているため）。
--   書き込みは admin のみ。
alter table public.bukken_teacher_stores enable row level security;
alter table public.bukken_teacher_bench  enable row level security;

drop policy if exists "teacher_stores_read" on public.bukken_teacher_stores;
create policy "teacher_stores_read" on public.bukken_teacher_stores
  for select to authenticated
  using ( public.current_app_email() like '%@avend.co.jp' );

drop policy if exists "teacher_stores_write" on public.bukken_teacher_stores;
create policy "teacher_stores_write" on public.bukken_teacher_stores
  for all to authenticated
  using ( public.current_app_role() = 'admin' )
  with check ( public.current_app_role() = 'admin' );

drop policy if exists "teacher_bench_read" on public.bukken_teacher_bench;
create policy "teacher_bench_read" on public.bukken_teacher_bench
  for select to authenticated
  using ( public.current_app_email() like '%@avend.co.jp' );

drop policy if exists "teacher_bench_write" on public.bukken_teacher_bench;
create policy "teacher_bench_write" on public.bukken_teacher_bench
  for all to authenticated
  using ( public.current_app_role() = 'admin' )
  with check ( public.current_app_role() = 'admin' );

-- --- データ投入（HTMLに直書きしていたものをそのまま移す）--------------
insert into public.bukken_teacher_stores (name, ours, pref, city, model, road, success, area, rent, park, p3, p10, p20, p30, p40, indep, traf) values
  ('SELFURUGI旭川末広店', true, '北海道', '旭川市', '地方', true, '成功', 50, 80000, 2, 10409, 39763, 42088, 43191, 45965, 0.865, 20141),
  ('NOTIME伊予松前店', true, '愛媛県', '伊予郡', '地方', true, '成功', 130, 200000, 5, 5652, 66535, 80844, 83397, 86577, 0.769, 26057),
  ('SELFURUGI盛岡菜園店', true, '岩手県', '盛岡市', '地方', false, '成功', 55, 160000, 0, 18919, 46739, 56204, 60177, 69830, 0.669, 7229),
  ('SELFURUGI徳島沖浜店', true, '徳島県', '徳島市', '地方', true, '成功', 70, 200000, 3, 11074, 43138, 63269, 72024, 77387, 0.557, null),
  ('NOTIME福井店', true, '福井県', '福井市', '地方', true, '成功', 231, 348000, 15, 10744, 43795, 68233, 83750, 93106, 0.47, null),
  ('NOTIME山形店', true, '山形県', '山形市', '地方', true, '成功', 82.5, 200000, 5, 16964, 36403, 52385, 66765, 78329, 0.465, 25004),
  ('SELFURUGI富山田中町店', true, '富山県', '富山市', '地方', true, '成功', 55, 160000, 2, 15833, 55289, 80401, 107210, 127310, 0.434, 24587),
  ('NOTIME鎌ヶ谷店、SELFURUGI鎌ヶ谷店', true, '千葉県', '鎌ケ谷市', '都心郊外', true, '成功', 100, 300000, 14, 18547, 245631, 783348, 1620433, 2850962, 0.086, 25215),
  ('NOTIME熊谷店、SELFURUGI熊谷店', true, '埼玉県', '熊谷市', '都心郊外', true, '成功', 100, 400000, 5, 11300, 54355, 159834, 349186, 745004, 0.073, 12798),
  ('NOTIMEいわき店', true, '福島県', 'いわき市', '地方', true, '普通', 148.5, 208000, 7, 3917, 30518, 39415, 43325, 49234, 0.62, 25160),
  ('NOTIME浜松店', true, '静岡県', '浜松市', '大型地方（人口70万人以上）', true, '普通', 80, 150000, 3, 16808, 107376, 144377, 168485, 216841, 0.495, 14981),
  ('SELFURUGI浜松高林店', true, '静岡県', '浜松市', '大型地方（人口70万人以上）', true, '普通', 50, 90000, 3, 23914, 99089, 139009, 166498, 235871, 0.42, 28865),
  ('SELFURUGI宇都宮店', true, '栃木県', '宇都宮市', '地方', true, '普通', 70, 200000, 3, 19101, 76656, 119630, 161543, 231468, 0.331, 18190),
  ('SELFURUGI仙台吉成店', true, '宮城県', '仙台市', '大型地方（人口70万人以上）', true, '失敗', 55, 100000, 2, 14252, 155323, 222515, 244953, 276356, 0.562, null),
  ('SELFURUGI GARAGE静岡店', true, '静岡県', '静岡市', '大型地方（人口70万人以上）', true, '失敗', 100, 80000, 2, 18895, 88622, 109311, 138101, 185365, 0.478, 8586),
  ('NOTIME高松', true, '香川県', '高松市', '地方', true, '失敗', 100, 110000, 5, 13529, 55025, 73323, 101968, 124576, 0.442, 16811),
  ('NOTIME静岡店', true, '静岡県', '静岡市', '大型地方（人口70万人以上）', true, '失敗', 60, 150000, 3, 18235, 79775, 94581, 140276, 195994, 0.407, 22421),
  ('SELFURUGI倉敷店', true, '岡山県', '倉敷市', '地方', true, '失敗', 50, 150000, 2, 12850, 63641, 152793, 204112, 241276, 0.264, null),
  ('SELFURUGI上田原店', true, '長野県', '上田市', '地方', true, '失敗', 55, 80000, 4, 6318, 22882, 31762, 54285, 108278, 0.211, 13354),
  ('SELFURUGI伊勢崎店', true, '群馬県', '伊勢崎市', '地方', true, '失敗', 50, 100000, 4, 9243, 64410, 187979, 286272, 359266, 0.179, 29013),
  ('NOTIME長野店', true, '長野県', '長野市', '地方', true, null, 100, 180000, 8, 9190, 43070, 59382, 68272, 83339, 0.517, 22324),
  ('NOTIME所沢店', true, '埼玉県', '所沢市', '都心郊外', true, null, 66, 250000, 6, 20128, 169728, 706427, 1837222, 3464705, 0.049, 9330),
  ('ヒューマンイズデッド本店', false, '岐阜県', '本巣市', '地方', true, '成功', 100, 80000, 5, 6019, 67354, 149174, 261079, 471368, 0.143, null),
  ('ヒューマンイズデッド長良店', false, '岐阜県', '岐阜市', '地方', true, '成功', 70, 150000, 5, 12901, 76307, 186080, 326202, 627365, 0.122, null),
  ('Syndi 豊川', false, '愛知県', '豊川市', '地方', true, '成功', 130, 300000, 7, 7144, 47470, 122433, 221258, 399919, 0.119, null),
  ('Syndi 小山', false, '栃木県', '小山市', '地方', true, '成功', 100, 200000, 5, 4993, 42953, 104176, 217606, 469476, 0.091, null)
on conflict (name) do update set
  ours = excluded.ours,
  pref = excluded.pref,
  city = excluded.city,
  model = excluded.model,
  road = excluded.road,
  success = excluded.success,
  area = excluded.area,
  rent = excluded.rent,
  park = excluded.park,
  p3 = excluded.p3,
  p10 = excluded.p10,
  p20 = excluded.p20,
  p30 = excluded.p30,
  p40 = excluded.p40,
  indep = excluded.indep,
  traf = excluded.traf,
  updated_at = now();

insert into public.bukken_teacher_bench (n, g, p3, p10, p20, p30, p40, traf, indep, muni) values
  ('鎌ヶ谷', 'urbanA', 18547, 245631, 783348, 1620433, 2850962, 25215, 0.086, '千葉県鎌ケ谷市'),
  ('旭川末広', 'road_success', 10409, 39763, 42088, 43191, 45965, 20141, 0.865, '北海道旭川市'),
  ('上田原', 'fail', 6318, 22882, 31762, 54285, 108278, 13354, 0.211, '長野県上田市'),
  ('仙台吉成', 'fail', 14252, 155323, 222515, 244953, 276356, null, 0.562, '宮城県仙台市'),
  ('伊勢崎', 'fail', 9243, 64410, 187979, 286272, 359266, 29013, 0.179, '群馬県伊勢崎市'),
  ('徳島沖浜', 'road_success', 11074, 43138, 63269, 72024, 77387, null, 0.557, '徳島県徳島市'),
  ('熊谷', 'urbanA', 11300, 54355, 159834, 349186, 745004, 12798, 0.073, '埼玉県熊谷市'),
  ('富山田中町', 'road_success', 15833, 55289, 80401, 107210, 127310, 24587, 0.434, '富山県富山市'),
  ('倉敷', 'fail', 12850, 63641, 152793, 204112, 241276, null, 0.264, '岡山県倉敷市'),
  ('静岡', 'fail', 18235, 79775, 94581, 140276, 195994, 22421, 0.407, '静岡県静岡市'),
  ('GARAGE静岡', 'fail', 18895, 88622, 109311, 138101, 185365, 8586, 0.478, '静岡県静岡市'),
  ('伊予松前', 'road_success', 5652, 66535, 80844, 83397, 86577, 26057, 0.769, '愛媛県伊予郡'),
  ('山形', 'road_success', 16964, 36403, 52385, 66765, 78329, 25004, 0.465, '山形県山形市'),
  ('福井', 'road_success', 10744, 43795, 68233, 83750, 93106, null, 0.47, '福井県福井市'),
  ('高松', 'fail', 13529, 55025, 73323, 101968, 124576, 16811, 0.442, '香川県高松市')
on conflict (n) do update set
  g = excluded.g,
  p3 = excluded.p3,
  p10 = excluded.p10,
  p20 = excluded.p20,
  p30 = excluded.p30,
  p40 = excluded.p40,
  traf = excluded.traf,
  indep = excluded.indep,
  muni = excluded.muni,
  updated_at = now();

notify pgrst, 'reload schema';
