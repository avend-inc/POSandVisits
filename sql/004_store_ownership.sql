-- =====================================================================
--  店舗に「直営 / FC」の区分（ownership）を持たせる
-- =====================================================================
--
-- 【これは何？】
--   将来フランチャイズ(FC)店のデータを追加したときに、直営とFCを
--   分けて表示・比較できるようにするための区分列を stores に足す。
--   いまある3店（山形・いわき・福井）は「直営」。
--
-- 【使い方】
--   Supabase 管理画面 → SQL Editor にこの中身を貼って [Run]。
--   （何度実行しても安全。既にあれば何もしない）
--
-- 【FC店を足すとき】
--   データ取り込みで店舗が自動追加されたら、その店だけ下記のように
--   'FC' に変更する（名前は実際の店舗名に置き換え）:
--     update stores set ownership = 'FC' where name = '（FC店の名前）';
-- =====================================================================

-- ownership 列を追加（既定は '直営'）。既存の3店は自動的に '直営' になる。
alter table stores
  add column if not exists ownership text not null default '直営';

-- 値は '直営' か 'FC' のどちらか（表記ゆれ防止）。
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'stores_ownership_chk'
  ) then
    alter table stores
      add constraint stores_ownership_chk check (ownership in ('直営','FC'));
  end if;
end $$;

-- 確認（任意）
-- select id, name, ownership from stores order by id;
