-- ============================================================
--  sql/017_inventory.sql
--  直営店の「月次棚卸」を、アプリの棚卸タブから記録・表示するためのテーブル。
--
--  ねらい：月ごとに「理論在庫（前月在庫＋入荷−販売等）」と「実地棚卸（実際に
--  数えた在庫）」を突き合わせ、GAP（差異＝ロス等）を出す。
--    ・KPI自動 … 販売点数・レジ袋・クーポンは売上データ(data.json)から自動。
--    ・手入力  … 入荷・発送・ロス・キャンペーン・詰め放題・実地棚卸（カテゴリ別）。
--
--  【使い方】Supabase → SQL Editor に貼って [Run]。何度実行しても安全。
--  前提: 004(stores) / 005(current_app_role, current_app_email) 実行済み。
-- ============================================================

create table if not exists public.inventory (
  store_id       bigint not null references public.stores(id) on delete cascade,
  ym             text   not null,                 -- 対象月 'YYYY-MM'

  -- 入荷（手入力）
  received_qty   numeric not null default 0,      -- 入荷着数
  received_price numeric not null default 0,      -- 入荷平均単価（税抜）

  -- 月中の在庫減少のうち、売上データから取れない分（手入力）
  shipping_qty   numeric not null default 0,      -- 発送数（EC等）
  loss_qty       numeric not null default 0,      -- ロス
  campaign_qty   numeric not null default 0,      -- キャンペーン(MEO用)
  tsume_bags     numeric not null default 0,      -- 詰め放題（袋数）
  tsume_qty      numeric not null default 0,      -- 詰め放題（着数）
  special_plus   numeric not null default 0,      -- 実地の特例（プラス分）
  special_minus  numeric not null default 0,      -- 実地の特例（マイナス分）

  -- 初月のみ：前月からの繰越が無い場合の初期在庫（通常は null＝前月の実地から自動繰越）
  opening_qty    numeric,                         -- 初期 前月末在庫（着数）
  opening_price  numeric,                         -- 初期 前月末平均単価

  -- 実地棚卸：ブロック×カテゴリの数量。
  --   例 {"店内":{"デニム":119,"Tシャツ":1198,...},"BY":{...},"倉庫":{...},
  --       "小物":{"ネックレス":3,"指輪":5,"ラグ":0,"その他小物":2}}
  physical       jsonb  not null default '{}'::jsonb,

  -- 保存時に確定させる当月の合算平均単価（＝翌月の「前月平均単価」に使う。
  -- 前月チェーンを毎回たどらずに済ませるためのキャッシュ）。
  avg_price      numeric,

  note           text,
  updated_at     timestamptz not null default now(),
  updated_by     text,
  primary key (store_id, ym)
);

create index if not exists inventory_store_idx on public.inventory (store_id);

alter table public.inventory enable row level security;

-- 棚卸は現場（許可リストに居る社内メンバー）が入力する。ログイン済みで
-- app_users に登録されている人（current_app_role() が非null）は読み書き可。
grant select, insert, update, delete on public.inventory to authenticated;

drop policy if exists inv_sel on public.inventory;
create policy inv_sel on public.inventory for select to authenticated
  using (public.current_app_role() is not null);

drop policy if exists inv_ins on public.inventory;
create policy inv_ins on public.inventory for insert to authenticated
  with check (public.current_app_role() is not null);

drop policy if exists inv_upd on public.inventory;
create policy inv_upd on public.inventory for update to authenticated
  using (public.current_app_role() is not null)
  with check (public.current_app_role() is not null);

drop policy if exists inv_del on public.inventory;
create policy inv_del on public.inventory for delete to authenticated
  using (public.current_app_role() = 'admin');

-- 更新時刻の自動更新（任意）。既存の set_updated_at 関数があれば使う。
do $$
begin
  if exists (select 1 from pg_proc where proname = 'set_updated_at') then
    drop trigger if exists inventory_touch on public.inventory;
    create trigger inventory_touch before update on public.inventory
      for each row execute function public.set_updated_at();
  end if;
end $$;
