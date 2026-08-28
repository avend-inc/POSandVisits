-- =====================================================================
--  sql/034_line.sql
--  LINE公式アカウントの配信実績・友だち数をためる
-- =====================================================================
--
-- 【これは何？】
--   店舗ごとのLINE公式アカウントについて、
--     ・配信ごとの成果（通数・開封・クリック）      … line_broadcasts
--     ・友だち数／ブロック数の日次スナップショット   … line_daily
--     ・友だち追加の経路別、クーポンの使用           … line_daily / line_broadcasts の列
--   を取り込んで貯める。売上(sales)・広告(meta_insights_daily)と
--   同じ日付軸で並べて見るための土台。
--
-- 【なぜ日次スナップショットが要るか】
--   Instagram のフォロワー数(sql/023)と同じで、「友だち数」は
--   その時点の値しか取れず、後から過去に遡れない。毎日1回とっておかないと
--   二度と手に入らない。配信実績(line_broadcasts)は配信IDで過去に遡れるので、
--   こちらは初回に全期間を一括で取り込む。
--
-- 【店舗との紐付け】
--   line_accounts.store_id に POS店舗(stores.id)を入れる。
--   売上・来店と同じ持ち方なので、配信の翌日に売上が動いたかを店舗単位で並べられる。
--   ※「店舗」の一覧は3つあって取り違えやすい（発送先 / POS店舗 / 加盟店の割り当て）。
--     ここで持つのは POS店舗（stores）＝売上・来店と同じ軸。
--   紐付けは後から埋められるよう、最初は NULL でよい。
--
-- 【権限】
--   取り込みは service_role（GitHub Actions）だけ。画面からは読み取りのみで、
--   RLSで「本部（is_hq）」に限定する。加盟店(社外)には見せない。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
--   前提: sql/019（is_hq / can_view_store）を実行済み。
-- =====================================================================

-- --- ① アカウント台帳 -------------------------------------------------
--   ETLが見つけたアカウントを upsert する。store_id だけは人が埋める。
create table if not exists public.line_accounts (
  account_id   text primary key,          -- 配信元でのアカウントID（ネブラスカ側のID）
  name         text,                      -- 表示名（NOTIME福井店 等）
  basic_id     text,                      -- LINEのベーシックID（@xxxxxxx）
  store_id     bigint,                    -- POS店舗（stores.id）。未紐付けは NULL
  active       boolean not null default true,
  first_seen_at timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists line_accounts_store_idx on public.line_accounts (store_id);

-- --- ② 配信ごとの成果 -------------------------------------------------
--   1行 = 1配信。取り直しても上書きになるよう配信IDを主キーにする。
create table if not exists public.line_broadcasts (
  broadcast_id text primary key,          -- 配信ID（配信元での一意なID）
  account_id   text not null references public.line_accounts(account_id) on delete cascade,
  sent_at      timestamptz,               -- 配信日時
  business_date date,                     -- 配信日（JST）。売上と突き合わせる軸
  title        text,                      -- 配信名／メッセージ名
  kind         text,                      -- 一斉配信 / セグメント配信 / ステップ配信 など
  target       text,                      -- 配信対象の絞り込み（あれば）
  delivered    integer,                   -- 配信通数
  opened       integer,                   -- 開封数（インプレッション）
  clicked      integer,                   -- クリック数
  click_users  integer,                   -- クリックした人数（ユニーク）
  blocked      integer,                   -- この配信きっかけのブロック数
  coupon_used  integer,                   -- 配信したクーポンの使用数
  synced_at    timestamptz not null default now()
);
create index if not exists line_broadcasts_date_idx on public.line_broadcasts (business_date);
create index if not exists line_broadcasts_acct_idx on public.line_broadcasts (account_id, business_date);

-- --- ③ 友だち数の日次スナップショット --------------------------------
create table if not exists public.line_daily (
  date          date not null,
  account_id    text not null references public.line_accounts(account_id) on delete cascade,
  friends       integer,                  -- 友だち数（有効＝ブロックを除く）
  followers     integer,                  -- 追加された累計（ブロック込み）
  added         integer,                  -- その日の新規追加
  blocked       integer,                  -- その日のブロック
  net           integer,                  -- 純増（added - blocked）
  targeted      integer,                  -- ターゲットリーチ（配信可能人数）
  synced_at     timestamptz not null default now(),
  primary key (date, account_id)
);
create index if not exists line_daily_date_idx on public.line_daily (date);

-- --- ④ 友だち追加の経路別 --------------------------------------------
--   1行 = 1アカウント × 1日 × 経路。経路の種類は配信元によって変わるので
--   列にせず行で持つ（増えても表を作り替えなくていい）。
create table if not exists public.line_sources (
  date       date not null,
  account_id text not null references public.line_accounts(account_id) on delete cascade,
  source     text not null,               -- 検索 / QRコード / URL / 友だちからの紹介 など
  added      integer,
  synced_at  timestamptz not null default now(),
  primary key (date, account_id, source)
);
create index if not exists line_sources_date_idx on public.line_sources (date);

-- --- RLS：本部だけ読める。書き込みは service_role のみ ----------------
alter table public.line_accounts   enable row level security;
alter table public.line_broadcasts enable row level security;
alter table public.line_daily      enable row level security;
alter table public.line_sources    enable row level security;

drop policy if exists line_accounts_read   on public.line_accounts;
drop policy if exists line_broadcasts_read on public.line_broadcasts;
drop policy if exists line_daily_read      on public.line_daily;
drop policy if exists line_sources_read    on public.line_sources;

create policy line_accounts_read   on public.line_accounts   for select to authenticated using ( public.is_hq() );
create policy line_broadcasts_read on public.line_broadcasts for select to authenticated using ( public.is_hq() );
create policy line_daily_read      on public.line_daily      for select to authenticated using ( public.is_hq() );
create policy line_sources_read    on public.line_sources    for select to authenticated using ( public.is_hq() );

-- 書き込みポリシーは作らない。service_role（GitHub Actions）はRLSを素通りするので、
-- ポリシーが無い＝画面からは一切書けない、になる。

notify pgrst, 'reload schema';

-- 確認（任意）:
--   select a.name, count(*) 配信数, max(b.business_date) 直近
--     from public.line_accounts a
--     left join public.line_broadcasts b on b.account_id = a.account_id
--    group by a.name order by 1;
--   -- 店舗との紐付け（POS店舗id は select id,name from stores で確認）:
--   -- update public.line_accounts set store_id = 24 where account_id = 'xxxx';
