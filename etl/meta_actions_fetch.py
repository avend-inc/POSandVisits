"""Meta広告のアクション系（プロフアクセス数・フォロー数・いいね数・平均視聴時間）を取り込む。

    python -m etl.meta_actions_fetch                          # 前日ぶん
    python -m etl.meta_actions_fetch --date 2026-08-09
    python -m etl.meta_actions_fetch --since 2025-01-01 --until 2026-08-09   # 過去分
    python -m etl.meta_actions_fetch --dry-run                # 取得だけしてDBに書かない

【これは何をするものか】
  meta_insights_daily には、広告費・表示回数・クリックは入っているのに
  follows / profile_visits（フォロー数・プロフアクセス数）が空のままになっている。
  そこを、この取り込みが Meta の insights から取って埋める。
  あわせて likes（いいね数）と video_avg_seconds（平均視聴時間）も入れる。
  ・いいね数    … actions の post_reaction（候補は下の LIKE_CANDS）
  ・平均視聴時間 … video_avg_time_watched_actions（actions とは別のフィールド。秒）

【行は作らない。既にある行の列だけ埋める】
  meta_insights_daily の持ち主は別の取り込み（avend-meta-ads）で、そちらが
  毎回まるごと書き直している。こちらが行を足すと、二重になったり、広告費が
  空の幽霊行ができたりする。なので UPDATE だけにして、INSERT はしない。
  片方が動かなくても、もう片方が壊れない作りにしてある。

【action_type の名前について（ここが肝）】
  プロフアクセス数・フォロー数は insights の `actions` 配列の中に入るが、
  そのキー(action_type)の正確な名前は公開ドキュメントから確定できなかった。
  しかも Meta は同じ出来事を名前違いで複数返すことがある。

  そこで「候補を優先順に並べて、実際に返ってきたものを使う」作りにした。
  ・どの action_type を採用したかを毎回ログに出す
  ・候補がどれも見つからなければ、何も書かずに、返ってきた action_type を
    全部並べて終わる（黙って0を書き込むのがいちばん危ないため）
  ・候補は環境変数で差し替えられる（META_FOLLOW_ACTIONS / META_PROFILE_ACTIONS）

【答え合わせ】
  Notimeつくば 2026-07-07 … 広告費 1,800円 / プロフアクセス 135 / フォロー 36
  取り込んだあと、この日この店の数字が合っているかを見れば確かめられる。

【必要な環境変数（GitHub Secrets）】
  META_ACCESS_TOKEN … ads_read 権限つきのアクセストークン
  META_API_VERSION  … 任意。既定 v21.0
  META_AD_ACCOUNTS  … 任意。act_123,act_456 と直接指定する場合
  SUPABASE_URL / SUPABASE_SERVICE_KEY … 保存先
  ※ トークンが無ければ何もせず正常終了する（設定前でも壊れない）。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import requests

from .settings import load_dotenv, validate_date, yesterday_jst
from .supabase_client import Supabase

API = (os.environ.get("META_API_VERSION") or "v21.0").strip()
GRAPH = f"https://graph.facebook.com/{API}"
TIMEOUT = 90

# 採用する action_type の候補。上から順に、実際に返ってきたものを使う。
# 環境変数でまるごと差し替えられる（正解が分かったら1つに絞ってよい）。
def _cands(env: str, default: list[str]) -> list[str]:
    v = (os.environ.get(env) or "").strip()
    return [x.strip() for x in v.split(",") if x.strip()] if v else default


FOLLOW_CANDS = _cands("META_FOLLOW_ACTIONS", [
    "onsite_conversion.follow",
    "onsite_conversion.ig_follow",
    "ig_follow",
    "follow",
])
PROFILE_CANDS = _cands("META_PROFILE_ACTIONS", [
    "onsite_conversion.ig_profile_visit",
    "onsite_conversion.profile_visit",
    "ig_profile_visit",
    "profile_visit",
    "onsite_conversion.instagram_profile_visit",
])
# いいね（投稿へのリアクション）。Metaは post_reaction を返すことが多いが、
# like を返す環境もあるので、フォロー数と同じく候補から選ぶ。
LIKE_CANDS = _cands("META_LIKE_ACTIONS", [
    "post_reaction",
    "like",
    "onsite_conversion.post_save",
])


def _token() -> str:
    return (os.environ.get("META_ACCESS_TOKEN") or "").strip()


def _get(path: str, params: dict) -> dict:
    p = dict(params)
    p["access_token"] = _token()
    r = requests.get(f"{GRAPH}/{path.lstrip('/')}", params=p, timeout=TIMEOUT)
    body = {}
    try:
        body = r.json()
    except Exception:                            # noqa: BLE001
        pass
    if r.status_code >= 400 or "error" in body:
        err = body.get("error") or {}
        raise RuntimeError(f"HTTP {r.status_code} {err.get('type','')} "
                           f"{err.get('code','')} {err.get('message','')[:200]}")
    return body


def _paged(path: str, params: dict) -> list[dict]:
    out: list[dict] = []
    body = _get(path, params)
    out.extend(body.get("data") or [])
    nxt = (body.get("paging") or {}).get("next")
    while nxt:
        r = requests.get(nxt, timeout=TIMEOUT)
        if r.status_code >= 400:
            break
        body = r.json()
        out.extend(body.get("data") or [])
        nxt = (body.get("paging") or {}).get("next")
    return out


def accounts() -> list[dict]:
    fixed = [a.strip() for a in (os.environ.get("META_AD_ACCOUNTS") or "").split(",") if a.strip()]
    if fixed:
        return [{"id": a, "name": a} for a in fixed]
    return _paged("me/adaccounts", {"fields": "id,name", "limit": "200"})


def insights(act: str, since: str, until: str) -> list[dict]:
    """日 × キャンペーンで取る。meta_insights_daily と同じ粒度にそろえる。

    use_unified_attribution_setting=true は「広告セットの計測設定に従う」指定で、
    Ads Manager の画面に出ている数字とそろえるために付けている。
    """
    return _paged(f"{act}/insights", {
        "level": "campaign",
        "time_increment": "1",
        "time_range": f'{{"since":"{since}","until":"{until}"}}',
        # video_avg_time_watched_actions は actions とは別のフィールド。
        # 動画でない広告では返ってこないので、無くても壊れないようにしてある。
        "fields": ("date_start,campaign_id,campaign_name,actions,"
                   "video_avg_time_watched_actions"),
        "use_unified_attribution_setting": "true",
        "limit": "500",
    })


def pick(seen: set[str], cands: list[str]) -> str | None:
    """候補のうち、実際に返ってきたものを優先順に1つ選ぶ。"""
    for c in cands:
        if c in seen:
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Meta広告のプロフアクセス数・フォロー数を取り込む")
    ap.add_argument("--date", help="対象日 YYYY-MM-DD（省略時は前日）")
    ap.add_argument("--since", help="過去分の開始日 YYYY-MM-DD")
    ap.add_argument("--until", help="過去分の終了日 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="取得だけしてDBに書かない")
    args = ap.parse_args()

    load_dotenv()
    if not _token():
        print("ℹ️ META_ACCESS_TOKEN が未設定のため、何もしません（設定前でも安全に終了します）。")
        return 0

    if args.since:
        since = validate_date(args.since)
        until = validate_date(args.until) if args.until else yesterday_jst()
    else:
        since = until = validate_date(args.date) if args.date else yesterday_jst()
    if since > until:
        print(f"❌ 期間が逆です: {since} 〜 {until}")
        return 1

    print("=" * 66)
    print(f"Meta広告 アクション系の取り込み  {since} 〜 {until} / API {API}")
    print("=" * 66)

    try:
        accts = accounts()
    except RuntimeError as e:
        print(f"❌ 広告アカウントが取れません: {e}")
        return 1
    print(f"  広告アカウント: {len(accts)}件")

    # ---- 取得 ----------------------------------------------------------
    # (日, キャンペーンID) → {action_type: 値}
    got: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # (日, キャンペーンID) → 平均視聴秒数。actions とは別に持つ
    vid: dict[tuple[str, str], float] = {}
    seen: set[str] = set()
    totals: dict[str, float] = defaultdict(float)
    failed = 0

    for a in accts:
        try:
            rows = insights(a["id"], since, until)
        except RuntimeError as e:
            failed += 1
            print(f"  ⚠️ {a.get('name') or a['id']}: {e}")
            continue
        for r in rows:
            d, cid = r.get("date_start"), r.get("campaign_id")
            if not d or not cid:
                continue
            for act in (r.get("actions") or []):
                t = act.get("action_type")
                if not t:
                    continue
                try:
                    v = float(act.get("value") or 0)
                except (TypeError, ValueError):
                    continue
                seen.add(t)
                totals[t] += v
                got[(d, str(cid))][t] += v
            # 平均視聴時間。配列で返るので、いちばん代表的な値（先頭）を採る。
            # ここは「平均」なので、日をまたいで足してはいけない（画面側で
            # 表示回数の重み付き平均にしている）。
            for w in (r.get("video_avg_time_watched_actions") or []):
                try:
                    vid[(d, str(cid))] = float(w.get("value") or 0)
                except (TypeError, ValueError):
                    pass
                break

    print(f"\n  取れた 日×キャンペーン: {len(got)}件 / action_type {len(seen)}種"
          + (f" / 失敗 {failed}アカウント" if failed else ""))

    # ---- どの action_type を使うか決める --------------------------------
    fk = pick(seen, FOLLOW_CANDS)
    pk = pick(seen, PROFILE_CANDS)
    lk = pick(seen, LIKE_CANDS)
    print(f"\n  フォロー数      に使う action_type: {fk or '（見つからず）'}")
    print(f"  プロフアクセス数 に使う action_type: {pk or '（見つからず）'}")
    print(f"  いいね数        に使う action_type: {lk or '（見つからず）'}")
    print(f"  平均視聴時間    が取れた 日×キャンペーン: {len(vid)}件")

    if not fk and not pk and not lk and not vid:
        # ここで0を書き込むと「本当に0だった」と読めてしまうので、何も書かない。
        print("\n❌ 候補の action_type がどれも返ってきませんでした。書き込みは行いません。")
        print("   返ってきた action_type は次のとおりです。この中から正しいものを選び、")
        print("   META_FOLLOW_ACTIONS / META_PROFILE_ACTIONS に指定してください：")
        for t, v in sorted(totals.items(), key=lambda x: -x[1])[:40]:
            print(f"     {t:<55} {v:>12,.0f}")
        return 1

    print(f"\n  期間合計: フォロー {totals.get(fk, 0):,.0f} / "
          f"プロフアクセス {totals.get(pk, 0):,.0f} / いいね {totals.get(lk, 0):,.0f}")

    if args.dry_run:
        print("\n  （--dry-run のためDBには書いていません）")
        return 0

    # ---- 既にある行の列だけ埋める（行は作らない）------------------------
    sb = Supabase()
    updated = missing = 0
    keys = sorted(set(got) | set(vid))
    for (d, cid) in keys:
        acts = got.get((d, cid), {})
        patch = {}
        if fk:
            patch["follows"] = int(acts.get(fk, 0))
        if pk:
            patch["profile_visits"] = int(acts.get(pk, 0))
        if lk:
            patch["likes"] = int(acts.get(lk, 0))
        if (d, cid) in vid:
            patch["video_avg_seconds"] = round(vid[(d, cid)], 2)
        if not patch:
            continue
        n = sb.update("meta_insights_daily",
                      {"date": f"eq.{d}", "campaign_id": f"eq.{cid}"}, patch)
        if n:
            updated += n
        else:
            missing += 1

    print(f"\n  更新 {updated}行"
          + (f" / 相手の行が無くて書けなかった {missing}件" if missing else ""))
    if missing:
        print("   （meta_insights_daily 側にその日のキャンペーン行がまだ無い場合です。"
              "先方の取り込みが走ったあと、もう一度流すと埋まります）")
    print("\n✅ 取り込みが終わりました。")
    print("   答え合わせ: Notimeつくば 2026-07-07 は"
          " 広告費1,800円 / プロフアクセス135 / フォロー36 が正解です。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
