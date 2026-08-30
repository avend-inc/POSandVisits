-- =====================================================================
--  sql/023_instagram.sql
--  Instagram（各店アカウント）のフォロワー数・インサイト・属性をためる
-- =====================================================================
--
-- 【これは何？】
--   店舗ごとのInstagramアカウントについて、Instagram Graph API から
--     ・フォロワー数（毎日のスナップショット）
--     ・リーチ・プロフィール表示などの日次インサイト
--     ・フォロワー属性（年齢・性別・都市・国）
--   を取り込んで貯める。広告(meta_insights_daily)と並べて見るための土台。
--
-- 【なぜスナップショットが要るか】
--   フォロワー「数」はAPIで過去に遡れない（その時点の値しか返らない）。
--   毎日1回とっておかないと、後からは二度と手に入らない。だから日次で貯める。
--
-- 【店舗との紐付け】
--   ig_accounts.destination_id に在庫アプリの destinations（店舗）を入れる。
--   広告データ(meta_insights_daily.destination_id)と同じ持ち方なので、
--   広告費とフォロワー増加を店舗単位で並べられる。
--   紐付けは後から画面/SQLで埋められるよう、最初は NULL でよい。
--
-- 【権限】
--   取り込みは service_role（GitHub Actions）だけ。画面からは読み取りのみで、
--   RLSで「AVENDメンバー（is_hq）」に限定する。加盟店(社外)には見せない。
--
-- 【使い方】Supabase 管理画面 → SQL Editor に貼って [Run]。何度実行しても安全。
--   前提: sql/019（is_hq）を実行済み。
-- =====================================================================

-- --- ① アカウント台帳 -------------------------------------------------
--   ETLがトークンで見えるアカウントを自動で見つけて upsert する。
--   destination_id（店舗）だけは人が埋める。
create table if not exists public.ig_accounts (
  ig_user_id     text primary key,          -- Instagram ビジネスアカウントID
  username       text,                      -- @なしのユーザー名（selfurugi_utsunomiya 等）
  name           text,                      -- 表示名（無人古着屋SELFURUGI宇都宮店 等）
  destination_id uuid,                      -- 店舗（在庫アプリの destinations.id）
  active         boolean not null default true,
  first_seen_at  timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

-- --- ② 日次スナップショット ------------------------------------------
--   1行 = 1アカウント × 1日。取り直しても上書きになるよう自然キーを張る。
create table if not exists public.ig_daily (
  date            date   not null,
  ig_user_id      text   not null references public.ig_accounts(ig_user_id) on delete cascade,
  followers_count integer,                  -- その日のフォロワー数（スナップショット）
  follows_count   integer,                  -- フォロー中の数
  media_count     integer,                  -- 投稿数
  reach           bigint,                   -- リーチ（インサイト・day）
  profile_views   bigint,                   -- プロフィールの表示回数
  accounts_engaged bigint,                  -- 反応したアカウント数
  website_clicks  bigint,                   -- ウェブサイトのタップ数
  total_interactions bigint,                -- インタラクション合計
  synced_at       timestamptz not null default now(),
  primary key (date, ig_user_id)
);
create index if not exists ig_daily_date_idx on public.ig_daily (date);
create index if not exists ig_daily_account_date_idx on public.ig_daily (ig_user_id, date desc);

-- --- ③ フォロワー属性 -------------------------------------------------
--   1行 = アカウント × 日 × 区分（age/gender/city/country）× 値。
--   例: breakdown='age', key='25-34', value=1234
create table if not exists public.ig_demographics (
  date       date not null,
  ig_user_id text not null references public.ig_accounts(ig_user_id) on delete cascade,
  breakdown  text not null,                 -- 'age' / 'gender' / 'city' / 'country'
  key        text not null,                 -- '25-34' / 'F' / 'Tokyo' 等
  value      bigint,
  synced_at  timestamptz not null default now(),
  primary key (date, ig_user_id, breakdown, key)
);

-- --- ④ 取り込みの実行記録（meta_sync_runs と同じ考え方）---------------
create table if not exists public.ig_sync_runs (
  id           bigserial primary key,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  target_date  date,
  accounts_ok  integer not null default 0,
  accounts_failed integer not null default 0,
  rows_upserted integer not null default 0,
  status       text,                        -- 'ok' / 'partial' / 'failed'
  message      text
);

create index if not exists ig_demographics_account_date_idx
  on public.ig_demographics (ig_user_id, date desc);
create index if not exists ig_accounts_destination_idx
  on public.ig_accounts (destination_id);

-- --- 権限：読み取りはAVENDメンバーのみ。書き込みは service_role だけ ---
alter table public.ig_accounts     enable row level security;
alter table public.ig_daily        enable row level security;
alter table public.ig_demographics enable row level security;
alter table public.ig_sync_runs    enable row level security;

-- 2026-05以降のSupabaseは新規テーブルをData APIに自動公開しない。
-- 画面は社内ログイン後の読み取りのみ、ETLはservice_roleのみ書き込める。
grant select on public.ig_accounts, public.ig_daily,
  public.ig_demographics, public.ig_sync_runs to authenticated;
grant select, insert, update, delete on public.ig_accounts, public.ig_daily,
  public.ig_demographics, public.ig_sync_runs to service_role;
grant usage, select on sequence public.ig_sync_runs_id_seq to service_role;

do $$
declare t text;
begin
  foreach t in array array['ig_accounts','ig_daily','ig_demographics','ig_sync_runs'] loop
    execute format('drop policy if exists "%s_read_hq" on public.%I', t, t);
    -- 読み取りだけ許可。書き込みポリシーは作らない＝画面からは一切書けない。
    -- （ETLは service_role キーなのでRLSを素通りする）
    execute format(
      'create policy "%s_read_hq" on public.%I for select to authenticated using (public.is_hq())',
      t, t);
  end loop;
end $$;

-- 確認（任意）
--   select count(*) from public.ig_accounts;
--   select date, count(*) from public.ig_daily group by 1 order by 1 desc limit 7;
