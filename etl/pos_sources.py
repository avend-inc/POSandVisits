"""
レジ接続(store_pos)を読み込み、パスワードを Vault から復号して返す。
自動取得(フェッチャ)から使う。service_role キー前提（get_pos_secret は service_role のみ実行可）。
"""
from __future__ import annotations

import requests

from .supabase_client import Supabase


def _secret(sb: Supabase, pos_id: int) -> str | None:
    """get_pos_secret RPC を呼んで、復号済みパスワードを取得する。"""
    url = f"{sb.url}/rest/v1/rpc/get_pos_secret"
    resp = requests.post(
        url,
        headers={"apikey": sb.key, "Authorization": f"Bearer {sb.key}",
                 "Content-Type": "application/json"},
        json={"p_id": pos_id}, timeout=60,
    )
    if resp.status_code != 200:
        # 失敗理由を表示（本文にPWは含まれない＝失敗応答なので安全）。
        print(f"    ⚠️ get_pos_secret(#{pos_id}) HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    try:
        val = resp.json()
    except ValueError:
        val = (resp.text or "").strip().strip('"')
    if not val:
        # 200だが空 = Vaultの復号ビューが行を返していない（連携/権限の切り分け用）。
        print(f"    ⚠️ get_pos_secret(#{pos_id}) 200だが空応答: {resp.text[:120]!r}")
    return val or None


def load_connections(sb: Supabase, pos_type: str | None = None,
                     only_active: bool = True) -> list[dict]:
    """
    レジ接続の一覧を返す。各要素に復号済み login_pw を含む。
      [{id, store_id, pos_type, pos_name, url, login_id, active, login_pw}, ...]
    """
    params = {"select": "id,store_id,pos_type,pos_name,url,login_id,active,pw_secret_id",
              "order": "id"}
    if pos_type:
        params["pos_type"] = f"eq.{pos_type}"
    if only_active:
        params["active"] = "eq.true"

    rows = sb.select("store_pos", params)
    out: list[dict] = []
    for r in rows:
        pw = _secret(sb, r["id"]) if r.get("pw_secret_id") else None
        out.append({
            "id": r["id"], "store_id": r.get("store_id"),
            "pos_type": r["pos_type"], "pos_name": r.get("pos_name") or "",
            "url": r.get("url"), "login_id": r.get("login_id"),
            "active": r.get("active", True), "login_pw": pw,
            "pw_secret_id": r.get("pw_secret_id"),  # 診断用（Vault連携の有無）
        })
    return out
