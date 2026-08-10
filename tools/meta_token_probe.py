"""Meta広告のアクセストークンが、この環境から使えるかを調べる（読み取りのみ）。

    python -m tools.meta_token_probe

【何のため】
  follows / profile_visits（プロフアクセス数・フォロー数）は、いまDBの列は
  あるが値が空。取得しているのは別リポジトリ(avend-meta-ads)で、こちらからは
  中を見られない。こちらで取りにいけるようにするには、Meta広告を読める
  アクセストークンが要る。まず「もう在るのか、無いのか」をはっきりさせる。

【見るもの】
  ① Supabaseの vault に meta/facebook 系の秘密が置かれていないか（名前だけ）
  ② 環境変数 META_ACCESS_TOKEN があるか
  ③ あれば、そのトークンで広告アカウントが見えるか（件数と名前だけ）

  秘密の中身は出さない。書き込みもしない。
"""
from __future__ import annotations

import os
import re
import sys

import requests

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
SB_TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
META_TOKEN = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
API = (os.environ.get("META_API_VERSION") or "v21.0").strip()


def sql(q: str):
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {SB_TOKEN}", "Content-Type": "application/json"},
        json={"query": q}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def main() -> int:
    print("=" * 60)
    print("Meta広告トークンの在り処を調べる")
    print("=" * 60)

    # ① vault に meta/facebook 系の秘密が置かれていないか（名前だけ）
    print("\n===== ① Supabase vault の秘密（名前だけ）=====")
    try:
        rows = sql("""
            select name, to_char(created_at,'YYYY-MM-DD') as 作成
              from vault.secrets
             where name ~* 'meta|facebook|fb|ads|ig|instagram'
             order by name limit 50
        """)
        if rows:
            for r in rows:
                print(f"  {r.get('name')} （{r.get('作成')}）")
        else:
            print("  該当なし（meta/facebook系の秘密は置かれていません）")
    except Exception as e:                       # noqa: BLE001
        print(f"  ⚠️ 見られませんでした: {str(e)[:200]}")

    # ② 環境変数
    print("\n===== ② 環境変数 META_ACCESS_TOKEN =====")
    if META_TOKEN:
        print(f"  あり（長さ {len(META_TOKEN)} 文字。中身は出しません）")
    else:
        print("  なし")
        print("\n  → こちらから取りにいくには、ads_read 権限つきのトークンを")
        print("     GitHub の Secrets に META_ACCESS_TOKEN として登録してください。")
        return 0

    # ③ そのトークンで広告アカウントが見えるか
    print("\n===== ③ トークンで見える広告アカウント =====")
    try:
        r = requests.get(f"https://graph.facebook.com/{API}/me/adaccounts",
                         params={"fields": "id,name,account_status",
                                 "limit": "200", "access_token": META_TOKEN}, timeout=60)
        body = r.json()
        if r.status_code >= 400 or "error" in body:
            err = body.get("error") or {}
            print(f"  ❌ 取れません: HTTP {r.status_code} "
                  f"{err.get('type','')} {err.get('code','')} {err.get('message','')[:200]}")
            return 1
        accts = body.get("data") or []
        print(f"  {len(accts)}件")
        for a in accts[:30]:
            print(f"    {a.get('id')} | {a.get('name')}")
        if not accts:
            print("  （0件。トークンにこのユーザーの広告アカウントが紐付いていません）")
    except Exception as e:                       # noqa: BLE001
        print(f"  ❌ 通信に失敗: {str(e)[:200]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
