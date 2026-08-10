"""
Instagram（各店アカウント）のフォロワー数・インサイト・属性を取り込む。

    python -m etl.instagram_fetch                 # 前日ぶん
    python -m etl.instagram_fetch --date 2026-08-09
    python -m etl.instagram_fetch --demographics  # 属性も取る（既定は週1想定でオフ）
    python -m etl.instagram_fetch --dry-run       # 取得だけしてDBに書かない

【なぜ毎日走らせるのか】
  フォロワー「数」はAPIで過去に遡れない。その時点の値しか返らないので、
  毎日1回スナップショットを取っておかないと後から二度と手に入らない。

【取ってくるもの】
  ・アカウント台帳 … トークンで見えるFacebookページ→紐づくIGアカウントを自動で発見。
                     新しい店のアカウントが増えても、こちらは何もしなくてよい。
  ・日次 … followers_count / follows_count / media_count（ノードの現在値）
           reach / profile_views / accounts_engaged / website_clicks /
           total_interactions（インサイト・day）
  ・属性 … follower_demographics（age / gender / city / country）

【APIの版ずれについて】
  Instagram Graph API は版によって使える指標がよく変わる（impressions の廃止など）。
  そこで「指標ごとに独立して取りに行き、ダメだったものは記録して次へ進む」作りにした。
  1つの指標が使えなくなっても、他の数字は今までどおり入り続ける。
  取りに行く指標は IG_DAY_METRICS で環境変数から差し替えられる。

【必要な環境変数（GitHub Secrets）】
  IG_ACCESS_TOKEN … Metaアプリの長期アクセストークン
                    （instagram_basic / instagram_manage_insights /
                      pages_show_list / pages_read_engagement が要る）
  IG_API_VERSION  … 任意。既定 v21.0
  SUPABASE_URL / SUPABASE_SERVICE_KEY … 保存先
  ※ トークンが無ければ何もせず正常終了する（設定前でも壊れない）。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import requests

from .settings import JST, EtlError, load_dotenv, validate_date, yesterday_jst
from .supabase_client import Supabase

API_VERSION = (os.environ.get("IG_API_VERSION") or "v21.0").strip()
GRAPH = f"https://graph.facebook.com/{API_VERSION}"
TIMEOUT = 60

# インサイト（period=day）で取りに行く指標。環境変数でまるごと差し替えられる。
#   ここに無い指標を足したい／版が変わって使えなくなった、というときは
#   IG_DAY_METRICS="reach,profile_views" のようにカンマ区切りで渡す。
DAY_METRICS = [m.strip() for m in (os.environ.get("IG_DAY_METRICS") or
    "reach,profile_views,accounts_engaged,website_clicks,total_interactions").split(",") if m.strip()]

# ig_daily の列名（APIの指標名と同じにしてある）
DAY_COLUMNS = {"reach", "profile_views", "accounts_engaged",
               "website_clicks", "total_interactions"}

DEMO_BREAKDOWNS = ["age", "gender", "city", "country"]


def _token() -> str:
    return (os.environ.get("IG_ACCESS_TOKEN") or "").strip()


def _get(path: str, params: dict) -> dict:
    """Graph APIを1回叩く。エラーはそのまま例外にして呼び元で仕分ける。"""
    p = dict(params)
    p["access_token"] = _token()
    r = requests.get(f"{GRAPH}/{path.lstrip('/')}", params=p, timeout=TIMEOUT)
    body = {}
    try:
        body = r.json()
    except Exception:                      # noqa: BLE001（JSONでない応答も拾う）
        pass
    if r.status_code >= 400 or "error" in body:
        err = (body.get("error") or {})
        raise EtlError(
            f"Graph API {path} が失敗しました: HTTP {r.status_code} "
            f"{err.get('type','')} {err.get('code','')} {err.get('message','')[:200]}")
    return body


def _paged(path: str, params: dict) -> list[dict]:
    """次ページがある限り data を集める。"""
    out: list[dict] = []
    body = _get(path, params)
    out.extend(body.get("data") or [])
    nxt = ((body.get("paging") or {}).get("next"))
    while nxt:
        r = requests.get(nxt, timeout=TIMEOUT)
        if r.status_code >= 400:
            break
        body = r.json()
        out.extend(body.get("data") or [])
        nxt = ((body.get("paging") or {}).get("next"))
    return out


def discover_accounts() -> list[dict]:
    """トークンで見えるFacebookページから、紐づくIGビジネスアカウントを集める。

    店舗が増えてもここが自動で拾うので、IDの手入力は要らない。
    """
    pages = _paged("me/accounts", {
        "fields": "id,name,instagram_business_account{id,username,name}",
        "limit": "100"})
    accounts: dict[str, dict] = {}
    for pg in pages:
        iga = pg.get("instagram_business_account")
        if not iga or not iga.get("id"):
            continue
        accounts[iga["id"]] = {
            "ig_user_id": iga["id"],
            "username": iga.get("username"),
            "name": iga.get("name") or pg.get("name"),
        }
    return list(accounts.values())


def fetch_node(ig_user_id: str) -> dict:
    """フォロワー数など「現在値」を取る（過去に遡れないので毎日撮る）。"""
    body = _get(ig_user_id, {"fields": "followers_count,follows_count,media_count"})
    return {
        "followers_count": body.get("followers_count"),
        "follows_count": body.get("follows_count"),
        "media_count": body.get("media_count"),
    }


def fetch_day_insights(ig_user_id: str, date: str) -> tuple[dict, list[str]]:
    """日次インサイトを指標ごとに取る。使えない指標は飛ばして理由を返す。

    period=day は「その日の終わりまで」の値。since/until はUNIX秒で、
    until を対象日の翌日0時(JST)にすると対象日ぶんが返る。
    """
    out: dict = {}
    skipped: list[str] = []
    since = int(datetime.fromisoformat(f"{date}T00:00:00+09:00").timestamp())
    until = since + 86400
    for metric in DAY_METRICS:
        if metric not in DAY_COLUMNS:
            skipped.append(f"{metric}(列が無いので保存しません)")
            continue
        try:
            body = _get(f"{ig_user_id}/insights", {
                "metric": metric, "period": "day",
                "since": str(since), "until": str(until)})
        except EtlError as e:
            skipped.append(f"{metric}({str(e)[:80]})")
            continue
        for row in (body.get("data") or []):
            vals = row.get("values") or []
            if vals and vals[0].get("value") is not None:
                out[metric] = vals[0]["value"]
    return out, skipped


def fetch_demographics(ig_user_id: str) -> tuple[list[dict], list[str]]:
    """フォロワー属性（年齢・性別・都市・国）。フォロワーが少ないと返らない。"""
    rows: list[dict] = []
    skipped: list[str] = []
    for bd in DEMO_BREAKDOWNS:
        try:
            body = _get(f"{ig_user_id}/insights", {
                "metric": "follower_demographics", "period": "lifetime",
                "metric_type": "total_value", "breakdown": bd})
        except EtlError as e:
            skipped.append(f"{bd}({str(e)[:80]})")
            continue
        for row in (body.get("data") or []):
            tv = row.get("total_value") or {}
            for br in (tv.get("breakdowns") or []):
                for res in (br.get("results") or []):
                    keys = res.get("dimension_values") or []
                    if not keys:
                        continue
                    rows.append({"breakdown": bd, "key": str(keys[0]),
                                 "value": res.get("value")})
    return rows, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="Instagramのフォロワー数・インサイトを取り込む")
    ap.add_argument("--date", help="対象日 YYYY-MM-DD（省略時は前日）")
    ap.add_argument("--demographics", action="store_true", help="フォロワー属性も取る")
    ap.add_argument("--dry-run", action="store_true", help="取得だけしてDBに書かない")
    args = ap.parse_args()

    load_dotenv()
    if not _token():
        print("ℹ️ IG_ACCESS_TOKEN が未設定のため、何もしません（設定前でも安全に終了します）。")
        return 0

    date = validate_date(args.date) if args.date else yesterday_jst()
    started = datetime.now(JST)
    print("=" * 60)
    print(f"Instagram 取り込み  対象日: {date} / API {API_VERSION}")
    print("=" * 60)

    sb = None if args.dry_run else Supabase()

    try:
        accounts = discover_accounts()
    except EtlError as e:
        print(f"❌ アカウントの取得に失敗しました: {e}")
        if sb:
            sb.insert("ig_sync_runs", [{
                "started_at": started.isoformat(), "finished_at": datetime.now(JST).isoformat(),
                "target_date": date, "status": "failed", "message": str(e)[:2000]}])
        return 1

    print(f"  見つかったアカウント: {len(accounts)}件")
    if not accounts:
        print("  （トークンから見えるIGビジネスアカウントがありません。権限をご確認ください）")

    if sb and accounts:
        sb.upsert("ig_accounts", [{**a, "updated_at": datetime.now(JST).isoformat()}
                                  for a in accounts], on_conflict="ig_user_id")

    daily_rows: list[dict] = []
    demo_rows: list[dict] = []
    ok = failed = 0
    notes: list[str] = []

    for a in accounts:
        uid, label = a["ig_user_id"], (a.get("username") or a["ig_user_id"])
        try:
            node = fetch_node(uid)
        except EtlError as e:
            failed += 1
            print(f"  ❌ {label}: {e}")
            notes.append(f"{label}: {str(e)[:120]}")
            continue
        ins, skipped = fetch_day_insights(uid, date)
        daily_rows.append({"date": date, "ig_user_id": uid, **node, **ins,
                           "synced_at": datetime.now(JST).isoformat()})
        ok += 1
        extra = f" / 取れなかった指標: {', '.join(skipped)}" if skipped else ""
        print(f"  ✅ {label}: フォロワー{node.get('followers_count')}{extra}")
        if skipped:
            notes.extend(f"{label}: {s}" for s in skipped)

        if args.demographics:
            drows, dskip = fetch_demographics(uid)
            demo_rows.extend({"date": date, "ig_user_id": uid,
                              "synced_at": datetime.now(JST).isoformat(), **r} for r in drows)
            if dskip:
                notes.extend(f"{label}/属性: {s}" for s in dskip)

    upserted = 0
    if sb:
        if daily_rows:
            upserted += sb.upsert("ig_daily", daily_rows, on_conflict="date,ig_user_id")
        if demo_rows:
            upserted += sb.upsert("ig_demographics", demo_rows,
                                  on_conflict="date,ig_user_id,breakdown,key")
        status = "ok" if failed == 0 else ("partial" if ok else "failed")
        sb.insert("ig_sync_runs", [{
            "started_at": started.isoformat(),
            "finished_at": datetime.now(JST).isoformat(),
            "target_date": date, "accounts_ok": ok, "accounts_failed": failed,
            "rows_upserted": upserted, "status": status,
            "message": (" / ".join(notes))[:2000] or None}])

    print(f"\n  日次 {len(daily_rows)}件 / 属性 {len(demo_rows)}件 "
          f"→ 保存 {upserted}行（成功 {ok} / 失敗 {failed}）")
    if args.dry_run:
        print("  （--dry-run のためDBには書いていません）")
    print("\n✅ 取り込みが終わりました。" if failed == 0 else
          f"\n⚠️ {failed}件のアカウントで失敗しました（他は取り込み済み）。")
    return 0 if ok or not accounts else 1


if __name__ == "__main__":
    sys.exit(main())
