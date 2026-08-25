"""
店舗一覧の一括登録／更新（CSV1枚で 店マスタ＋レジ接続 をまとめて反映）

非公開バケットに置いた「店舗一覧CSV」を読み、各行について:
  1) stores を upsert（店名で照合。区分/コード/表示 などを更新）
  2) URL/ID/PW があれば store_pos（レジ接続）も upsert（PWはVaultに暗号化）

CSVの列（日本語/英語どちらでも。無い列は触らない）:
  store(店舗/店舗名)     … 必須。ダッシュボードの店名と一致（新規なら作成）
  code(コード)           … 店舗コード（英字）
  ownership(区分)        … 直営 / FC
  visible(表示)          … 1/0, true/false, 表示/非表示
  pos_type(レジ種類)     … ezregi(EZ/SIPOS) / airregi / cashier（既定 ezregi）
  url                    … レジ管理画面のURL（ログイン画面 or CSV出力画面）
  login_id(id)           … レジのログインID
  login_pw(pw/password)  … レジのログインPW（Vaultに暗号化保存）
  pos_name               … 表示名（例 SIPOS。同一店で複数レジを区別）
  active(稼働)           … 1/0（既定 true）

使い方（GitHub Actions「店舗一覧 一括登録」から）:
  python -m tools.import_stores --bucket imports --path stores.csv [--export]

※ PWを含むので必ず「非公開バケット」に置き、取り込み後はCSVを削除してください。
"""
from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.settings import EtlError, load_dotenv  # noqa: E402
from etl.supabase_client import Supabase  # noqa: E402


def _delete(sb: Supabase, bucket: str, path: str) -> bool:
    """取り込みが終わったCSVをバケットから消す。

    CSVにはレジのログインパスワードが平文で入っている。以前は最後に
    「手で消してください」と表示するだけで、消し忘れるとバケットに
    平文のまま残り続けた。取り込めた時点で自動で消す。
    """
    url = f"{sb.url}/storage/v1/object/{bucket}/{path}"
    r = requests.delete(url, headers={"apikey": sb.key,
                                      "Authorization": f"Bearer {sb.key}"}, timeout=60)
    return r.status_code in (200, 204)


def _download(sb: Supabase, bucket: str, path: str) -> bytes:
    url = f"{sb.url}/storage/v1/object/{bucket}/{path}"
    r = requests.get(url, headers={"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}, timeout=180)
    if r.status_code != 200:
        raise EtlError(f"CSVを取得できませんでした（HTTP {r.status_code}）: {r.text[:200]}\n"
                       f"  → 非公開バケット '{bucket}' に '{path}' があるか確認してください。")
    return r.content


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# 列名の揺れを吸収（小文字化して照合）
ALIASES = {
    "store": ["store", "店舗", "店舗名", "店名", "name"],
    "code": ["code", "コード", "店舗コード"],
    "ownership": ["ownership", "区分", "直営fc", "直営/fc"],
    "visible": ["visible", "表示", "表示/非表示", "ダッシュボード表示"],
    "pos_type": ["pos_type", "レジ種類", "レジ", "種類"],
    "url": ["url", "ＵＲＬ"],
    "login_id": ["login_id", "id", "ログインid", "ＩＤ"],
    "login_pw": ["login_pw", "pw", "password", "パスワード", "ログインpw", "ＰＷ"],
    "pos_name": ["pos_name", "表示名", "レジ名"],
    "active": ["active", "稼働", "有効"],
}


def _colmap(cols: list[str]) -> dict[str, str]:
    low = {c: str(c).strip().lower().replace(" ", "") for c in cols}
    out = {}
    for key, names in ALIASES.items():
        for c, l in low.items():
            if l in names:
                out[key] = c
                break
    return out


def _truthy(v) -> bool:
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on", "表示", "稼働", "有効", "○", "true")


def _pos_type(v) -> str:
    s = str(v).strip().lower()
    if s in ("ez", "ezレジ", "ezregi", "sipos", "si"):
        return "ezregi"
    if s in ("air", "airレジ", "airregi"):
        return "airregi"
    if s in ("cashier",):
        return "cashier"
    return "ezregi"


def _rpc_upsert_pos(sb: Supabase, store_id: int, pos_type: str, pos_name: str,
                    url: str, login_id: str, login_pw: str, active: bool) -> None:
    r = requests.post(
        f"{sb.url}/rest/v1/rpc/service_upsert_pos",
        headers={"apikey": sb.key, "Authorization": f"Bearer {sb.key}",
                 "Content-Type": "application/json"},
        json={"p_store_id": store_id, "p_pos_type": pos_type, "p_pos_name": pos_name or "",
              "p_url": url or None, "p_login_id": login_id or None,
              "p_pw": (login_pw or None), "p_active": active},
        timeout=60,
    )
    if r.status_code not in (200, 201, 204):
        raise EtlError(f"レジ接続の登録に失敗（HTTP {r.status_code}）: {r.text[:200]}\n"
                       "  → sql/015_service_upsert_pos.sql を実行済みか確認してください。")


def main() -> int:
    ap = argparse.ArgumentParser(description="店舗一覧の一括登録／更新")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--path", required=True)
    ap.add_argument("--export", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    sb = Supabase()

    print(f"CSVを取得: {args.bucket}/{args.path}")
    text = _decode(_download(sb, args.bucket, args.path))
    df = pd.read_csv(StringIO(text), dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    cm = _colmap(list(df.columns))
    if "store" not in cm:
        raise EtlError(f"『店舗（store/店舗名）』の列が見つかりません。実際の列: {list(df.columns)}")

    cache = sb.store_map()
    n_store = n_conn = n_pw = 0

    for _, row in df.iterrows():
        name = str(row[cm["store"]]).strip()
        if not name:
            continue
        store_id = sb.get_or_create_store(name, cache)

        # --- stores 側の更新（CSVにある列だけ） ---
        patch = {}
        if "code" in cm and str(row[cm["code"]]).strip():
            patch["code"] = str(row[cm["code"]]).strip()
        if "ownership" in cm and str(row[cm["ownership"]]).strip():
            own = str(row[cm["ownership"]]).strip()
            patch["ownership"] = "FC" if own.upper() == "FC" or "fc" in own.lower() else "直営"
        if "visible" in cm and str(row[cm["visible"]]).strip():
            patch["visible"] = _truthy(row[cm["visible"]])
        if patch:
            rr = requests.patch(
                f"{sb.url}/rest/v1/stores?id=eq.{store_id}",
                headers={"apikey": sb.key, "Authorization": f"Bearer {sb.key}",
                         "Content-Type": "application/json", "Prefer": "return=minimal"},
                json=patch, timeout=60)
            if rr.status_code in (200, 204):
                n_store += 1

        # --- レジ接続（URL か ID か PW があれば） ---
        url = str(row.get(cm.get("url", ""), "")).strip() if "url" in cm else ""
        lid = str(row.get(cm.get("login_id", ""), "")).strip() if "login_id" in cm else ""
        lpw = str(row.get(cm.get("login_pw", ""), "")).strip() if "login_pw" in cm else ""
        if url or lid or lpw:
            ptype = _pos_type(row[cm["pos_type"]]) if "pos_type" in cm else "ezregi"
            pname = str(row[cm["pos_name"]]).strip() if "pos_name" in cm else ""
            active = _truthy(row[cm["active"]]) if ("active" in cm and str(row[cm["active"]]).strip()) else True
            _rpc_upsert_pos(sb, store_id, ptype, pname, url, lid, lpw, active)
            n_conn += 1
            if lpw:
                n_pw += 1

    print(f"店マスタ更新: {n_store}行 / レジ接続 upsert: {n_conn}件（うちPW設定 {n_pw}件）")

    if args.export:
        print("ダッシュボードを更新します…")
        import sys as _sys
        from tools import export_dashboard
        saved = _sys.argv
        _sys.argv = [saved[0]]
        try:
            export_dashboard.main()
        finally:
            _sys.argv = saved

    # PWを平文で含むCSVを残さない
    if _delete(sb, args.bucket, args.path):
        print(f"✅ 一括登録が完了しました。取り込み済みCSV（{args.bucket}/{args.path}）は削除しました。")
    else:
        print(f"✅ 一括登録が完了しました。\n"
              f"⚠️ CSV（{args.bucket}/{args.path}）を自動削除できませんでした。"
              f"PWが平文で残るので、管理画面から手で削除してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
