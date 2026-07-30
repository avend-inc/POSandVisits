-- ============================================================
--  sql/014_category_grades.sql
--  カテゴリの季節区分（SS/AW/その他）と、グレード(ランク S/A/B/C/D)別 価格帯（税抜）。
--
--  ・カテゴリは SS（春夏）/ AW（秋冬）/ other（その他＝ランクなし）のどれかに属する。
--  ・商品のグレードは「その商品の税抜単価」がどの価格帯に入るかで決まる（帯はSS/AWで別）。
--  ・すき間の価格は下の帯に寄せる（＝下限しきい値で連続。集計は画面/exporter側で実施）。
--  ・比率イメージ（目標構成比）: ntm=NOTIME / sfg=SELFURUGI。あとで実績と比較する用。
--  前提: 005(current_app_role) 実行済み。何度実行しても安全（seedは upsert）。
-- ============================================================

-- ---- カテゴリ → 季節区分 --------------------------------------------------
create table if not exists public.category_season (
  category   text primary key,
  season     text not null check (season in ('SS','AW','other')),
  updated_at timestamptz not null default now()
);

alter table public.category_season enable row level security;
drop policy if exists category_season_read on public.category_season;
create policy category_season_read on public.category_season
  for select to authenticated using (public.current_app_role() is not null);
drop policy if exists category_season_admin on public.category_season;
create policy category_season_admin on public.category_season
  for all to authenticated
  using (public.current_app_role() = 'admin')
  with check (public.current_app_role() = 'admin');

-- ---- グレード(ランク)別 価格帯（税抜） ------------------------------------
create table if not exists public.grade_bands (
  season     text not null check (season in ('SS','AW')),
  grade      text not null,               -- S/A/B/C/D
  lo         integer not null,            -- 下限（税抜・円）含む
  hi         integer,                     -- 上限（税抜・円）含む。NULL=上限なし（表示・編集用）
  ntm_pct    numeric,                     -- 目標比率(NOTIME) %
  sfg_pct    numeric,                     -- 目標比率(SELFURUGI) %
  sort       integer not null default 0,  -- 表示順（S=1…D=5）
  updated_at timestamptz not null default now(),
  primary key (season, grade)
);

alter table public.grade_bands enable row level security;
drop policy if exists grade_bands_read on public.grade_bands;
create policy grade_bands_read on public.grade_bands
  for select to authenticated using (public.current_app_role() is not null);
drop policy if exists grade_bands_admin on public.grade_bands;
create policy grade_bands_admin on public.grade_bands
  for all to authenticated
  using (public.current_app_role() = 'admin')
  with check (public.current_app_role() = 'admin');

-- ---- 初期データ（seed）----------------------------------------------------
-- カテゴリ→季節区分
insert into public.category_season (category, season) values
  ('Tシャツ','SS'),
  ('バンド/ミュージックTシャツ','SS'),
  ('アニメ/ムービーTシャツ','SS'),
  ('プリントTシャツ','SS'),
  ('ポロシャツ','SS'),
  ('半袖シャツ','SS'),
  ('長袖シャツ','SS'),
  ('スウェット/パーカー','AW'),
  ('セーター/フリース','AW'),
  ('ニット','AW'),
  ('アウター/ジャケット','AW'),
  ('ヘビーアウター','AW'),
  ('デニム','AW'),
  ('チノ/スラックス','AW'),
  ('雑貨/小物','other'),
  ('福袋','other'),
  ('ミステリーBOX','other'),
  ('詰め放題','other')
on conflict (category) do update
  set season = excluded.season, updated_at = now();

-- グレード別 価格帯（税抜）＋目標比率
insert into public.grade_bands (season, grade, lo, hi, ntm_pct, sfg_pct, sort) values
  ('AW','S',7490,null,15,0,1),
  ('AW','A',5490,6980,30,20,2),
  ('AW','B',3490,4980,55,55,3),
  ('AW','C',1980,2980, 0,25,4),
  ('AW','D', 500,1490, 0, 0,5),
  ('SS','S',6490,null,15,0,1),
  ('SS','A',4490,5980,30,20,2),
  ('SS','B',2980,3980,55,55,3),
  ('SS','C',1980,2490, 0,25,4),
  ('SS','D', 500,1490, 0, 0,5)
on conflict (season, grade) do update
  set lo = excluded.lo, hi = excluded.hi,
      ntm_pct = excluded.ntm_pct, sfg_pct = excluded.sfg_pct,
      sort = excluded.sort, updated_at = now();

-- 確認（任意）
-- select * from public.category_season order by season, category;
-- select * from public.grade_bands order by season, sort;
