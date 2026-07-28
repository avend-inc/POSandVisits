-- ============================================================
--  sql/009_kpi_visitors.sql
--  「来店数をKPIに使うか」フラグ（kpi_visitors）を店舗に追加。
--
--  【背景】
--   下北沢は夜間だけデジテールを使うため、来店数が“夜間のみ”しか取れない。
--   → データは記録として貯めるが、来店数・購入率などのKPI集計には使わない
--     （全店合計や直営合計の購入率が実態とズレる「認識齟齬」を防ぐ）。
--
--  【使い方】Supabase → SQL Editor に貼って [Run]。何度実行しても安全。
--  前提: 004(ownership) を実行済み。
-- ============================================================

-- KPIに来店数を使うか（既定 true）。false の店は来店数・購入率のKPIから除外。
alter table public.stores
  add column if not exists kpi_visitors boolean not null default true;

-- 下北沢：直営／来店は「取得はするがKPIには使わない」／来店データ自体は集める。
--  ・ownership     = '直営'
--  ・has_entry_data = true  （夜間デジテールを集めるため。digitel_slug を入れると取得開始）
--  ・kpi_visitors  = false （来店数・購入率のKPIからは除外）
update public.stores
   set ownership      = '直営',
       has_entry_data = true,
       kpi_visitors   = false
 where code = 'shimokitazawa';

-- 直営の既存3店は KPIに来店を使う（既定 true のままでOK）。
-- FC店を足したら、その店の ownership を 'FC' に更新する:
--   update public.stores set ownership='FC' where name='（FC店名）';

-- 下北沢の夜間デジテールを取り込むには、デジテール側の識別名(slug)を入れる（分かり次第）:
--   update public.stores set digitel_slug='（下北沢のdigitel_slug）' where code='shimokitazawa';

-- 確認（任意）
-- select code, name, ownership, has_entry_data, kpi_visitors, digitel_slug from public.stores order by id;
