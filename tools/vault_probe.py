"""Vault(store_pos_*) の秘密が残っているか確認し、store_pos.id と再リンク可能かを診断（読み取り専用）。"""
from __future__ import annotations
import json, os, re
import requests
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=90)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def show(t,sql):
    print(f"\n===== {t} =====")
    print(json.dumps(run(sql),ensure_ascii=False,indent=1)[:6000])
def main():
    show("vault.secrets の store_pos_* 件数", """
      select count(*) 件数,
             min(created_at) 最古, max(created_at) 最新
      from vault.secrets where name like 'store_pos_%'""")
    show("vault: store_pos_* 一覧（PW本体は出さない）", """
      select name, (id is not null) 有,
             to_char(created_at,'YYYY-MM-DD') created, to_char(updated_at,'YYYY-MM-DD') updated
      from vault.secrets where name like 'store_pos_%' order by name""")
    show("再リンク可否：store_pos.id と vault名 store_pos_<id> の突合", """
      select sp.id, sp.pos_type, sp.pos_name,
             (sp.pw_secret_id is not null) 現pw有,
             (vs.id is not null) vault有,
             vs.id vault_secret_id
      from public.store_pos sp
      left join vault.secrets vs on vs.name = 'store_pos_'||sp.id
      order by sp.pos_type, sp.id""")
    show("再リンクで復旧できる件数の見込み", """
      select sp.pos_type,
             count(*) 総数,
             count(*) filter (where sp.pw_secret_id is null and vs.id is not null) 復旧可能,
             count(*) filter (where sp.pw_secret_id is not null) 既に設定済,
             count(*) filter (where vs.id is null) vault無し
      from public.store_pos sp
      left join vault.secrets vs on vs.name = 'store_pos_'||sp.id
      group by sp.pos_type order by sp.pos_type""")
    return 0
raise SystemExit(main())
