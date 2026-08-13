-- =====================================================================
--  sql/027_dashboard_tables.sql
--  ダッシュボードの集計結果を、JSONではなくテーブルに置く（第1段階）
-- =====================================================================
--
-- 【いまの作りと、その限界】
--   日次ETLが集計して data.json（10.5MB）を作り、Storageに置いている。
--   画面はそれを丸ごとダウンロードして、全部メモリに載せてから絞り込む。
--     ・毎回10.5MBを配る（回線の細い場所ではつらい）
--     ・加盟店の隔離を「別のJSONを配る」ことで担保している（アプリ任せ）
--     ・ETLが走るまで数字が変わらない
--
-- 【これで何が変わるか】
--   同じ集計結果をテーブルにも書く。テーブルなら RLS が効くので、
--   「加盟店には自店の行しか返さない」ことを Postgres 側で担保できる。
--   アプリの作り方に関係なく守られるのが大きい。
--
-- 【この段階では画面は変えない】
--   まずテーブルを作って、ETLが書くところまで。data.json はそのまま動く。
--   数字が一致することを確かめてから、画面を切り替える。
--
-- 【集計はPythonのまま】
--   SQLに書き直さない。いまの集計はPOSの純売上と1円まで合っていて、
--   レジ袋・クーポンの扱い、税抜の実額と近似の区別、2レジ店の内訳など、
--   細かい約束事が積み上がっている。移し替えると必ずどこかがずれる。
--   同じPythonが「JSONにも書くし、テーブルにも書く」形にする。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
--   前提: sql/019（is_hq / can_view_store）を実行済み。
-- =====================================================================

-- --- ① 日別×店舗（画面のほとんどがこれを見る）-------------------------
create table if not exists public.dash_daily (
  date              date   not null,
  store_id          bigint not null,
  sales_in          numeric,        -- 税込売上（POSの純売上そのまま）
  sales_ex          numeric,        -- 税抜売上。近似しか無い店日は null
  tx                integer,        -- 取引数（伝票数）
  items             numeric,        -- 販売点数。近似しか無い店日は null
  visitors          integer,        -- 来店数（無人＋有人の合算）
  bag_in            numeric,        -- レジ袋等（税込）
  bag_ex            numeric,        -- レジ袋等（税抜）
  bag_qty           numeric,        -- レジ袋等の点数
  komono_ex         numeric,        -- 小物の税抜売上（商品単価の分子から差し引く用）
  nocat_ex          numeric,        -- 明細なし実売上の税抜（点数の無い売上／商品単価の分子から差し引く）
  coupon_qty        numeric,        -- クーポン利用数
  coupon_amount     numeric,        -- クーポン割引額
  sales_ex_unmanned numeric,        -- 無人営業時間の税抜売上（2レジ店のみ）
  sales_ex_manned   numeric,        -- 有人営業時間の税抜売上（2レジ店のみ）
  visitors_staffed  integer,        -- 有人営業時間の来店数（手入力）
  updated_at        timestamptz not null default now(),
  primary key (date, store_id)
);
create index if not exists dash_daily_store_idx on public.dash_daily (store_id, date);

-- --- ② カテゴリ別（日×店×カテゴリ）------------------------------------
create table if not exists public.dash_category (
  date       date   not null,
  store_id   bigint not null,
  category   text   not null,
  amount     numeric,
  qty        numeric,
  updated_at timestamptz not null default now(),
  primary key (date, store_id, category)
);
create index if not exists dash_category_store_idx on public.dash_category (store_id, date);

-- --- ③ カテゴリ×価格帯（日×店×カテゴリ×価格）--------------------------
create table if not exists public.dash_category_price (
  date       date   not null,
  store_id   bigint not null,
  category   text   not null,
  price      numeric not null,
  amount     numeric,
  qty        numeric,
  updated_at timestamptz not null default now(),
  primary key (date, store_id, category, price)
);
create index if not exists dash_category_price_store_idx on public.dash_category_price (store_id, date);

-- --- ④ バンドル（SALE）別（日×店×コード）------------------------------
create table if not exists public.dash_bundle (
  date       date   not null,
  store_id   bigint not null,
  code       text   not null,
  n          integer,        -- 利用数（伝票数）
  amount     numeric,        -- 売上（親行合計）
  updated_at timestamptz not null default now(),
  primary key (date, store_id, code)
);
create index if not exists dash_bundle_store_idx on public.dash_bundle (store_id, date);

-- --- 権限：自分が見られる店の行だけ返す --------------------------------
--   can_view_store() は AVENDメンバーなら全店 true、加盟店なら割り当て店だけ true。
--   ここが効くので、画面の作りに関係なく他店の行は返らない。
--   書き込みポリシーは作らない＝画面からは一切書けない（ETLは service_role）。
do $$
declare t text;
begin
  foreach t in array array['dash_daily','dash_category','dash_category_price','dash_bundle'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists "%s_read" on public.%I', t, t);
    execute format(
      'create policy "%s_read" on public.%I for select to authenticated '
      'using (public.can_view_store(store_id))', t, t);
  end loop;
end $$;

-- 確認（任意）
--   select count(*) from public.dash_daily;           -- 期待 14,659 前後
--   select count(*) from public.dash_category;        -- 期待 40,108 前後
--   select count(*) from public.dash_category_price;  -- 期待 75,661 前後
