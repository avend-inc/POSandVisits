"""SIPOS(ezregi)接続のPW再入力チェックリスト。ホスト(テナント)ごとに、ログインに使う代表接続
(id最小)と、URLが壊れていないかを一覧化する。管理画面での再入力対象を洗い出す用（読み取り専用）。"""
from __future__ import annotations
import json, os, re
import requests
from urllib.parse import urlparse
URL=(os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN=(os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF=re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
def run(sql):
    r=requests.post(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},json={"query":sql},timeout=90)
    if r.status_code>=400: raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()
def host(u):
    u=(u or "").strip()
    if "://" not in u: u="https://"+u
    return (urlparse(u).netloc or "").lower()
def main():
    rows=run("""
      select sp.id, sp.store_id, s.name 店舗, sp.login_id, sp.url,
             (sp.pw_secret_id is not null) pw有
      from public.store_pos sp left join public.stores s on s.id=sp.store_id
      where sp.pos_type='ezregi' order by sp.id""")
    # ホストごとに集約
    groups={}
    for r in rows:
        h=host(r["url"]); groups.setdefault(h,[]).append(r)
    print(f"SIPOS(ezregi)接続: {len(rows)}件 / テナント(ホスト): {len(groups)}\n")
    print("【テナント別・再入力チェックリスト】 ★=このテナントの代表(ログインに使う=PW/URL必須)")
    need_pw=0; bad_url=0
    for h in sorted(groups):
        cs=sorted(groups[h], key=lambda x:x["id"])
        rep=cs[0]
        ok = "sipos.services" in (h or "")
        if not ok: bad_url+=1
        flag = "" if ok else "  ⚠️URL不正(sipos.servicesでない→要修正)"
        print(f"\n● テナント: {h or '(URL空)'}{flag}")
        for c in cs:
            star="★" if c["id"]==rep["id"] else "  "
            pw="PW済" if c["pw有"] else "PW未"
            if c["id"]==rep["id"] and not c["pw有"]: need_pw+=1
            stores=c["店舗"] or f"store_id={c['store_id']}"
            print(f"   {star} id={c['id']:>3}  {pw:<4}  login={c['login_id'] or '(空)'!s:<22} {stores}  url={c['url']}")
    print(f"\n--- サマリ ---")
    print(f"テナント数(=最低限入れ直すPW件数): {len(groups)}")
    print(f"代表接続でPW未設定: {need_pw}件 / URL不正テナント: {bad_url}件")
    print("※ 管理画面(レジ接続)で、各テナントの★の接続に『パスワード』を入力（URL不正は先に修正）。")
    print("※ ★以外の同ホスト接続はログインには使わないが、入れておいても害はありません。")
    return 0
raise SystemExit(main())
