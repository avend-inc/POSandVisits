"""Meta広告の insights が返す actions を、そのまま並べて見る（読み取りのみ）。

    python -m tools.meta_actions_probe
    python -m tools.meta_actions_probe --since 2026-07-07 --until 2026-07-13
    python -m tools.meta_actions_probe --campaign つくば

【何のため】
  プロフアクセス数・フォロー数は insights の `actions` 配列の中に入る。
  ところが、その中のキー（action_type）の正確な名前が、公開ドキュメントからは
  確定できなかった。しかも Meta は1つの出来事に対して名前違いの action_type を
  5〜8種類返すことがある（値は同じ）。当てずっぽうで拾うと、それらしい数字が
  出てしまい、間違っていても気づけない。

  そこで「推測して実装する」のではなく、まず実際に返ってくる action_type を
  ぜんぶ並べて、答え合わせしてから実装する。

【答え合わせの基準】
  スプレッドシートで確認済みの実測値：
    Notimeつくば 2026-07-07 … 広告費 1,800円 / プロフアクセス 135 / フォロー 36
  この数字と一致する action_type が正解。

【注意：計測期間の設定】
  Ads Manager の画面と数字をそろえるには、広告セットの計測設定に従わせる
  （use_unified_attribution_setting=true）必要がある。どちらが画面と合うか
  分からないので、両方とも取って並べて出す。

【必要な環境変数】
  META_ACCESS_TOKEN … ads_read 権限つきのアクセストークン
  META_API_VERSION  … 任意。既定 v21.0
  META_AD_ACCOUNTS  … 任意。act_123,act_456 のように直接指定する場合
  ※ トークンが無ければ、そのことを伝えて正常終了する。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import requests

API = (os.environ.get("META_API_VERSION") or "v21.0").strip()
GRAPH = f"https://graph.facebook.com/{API}"
TIMEOUT = 90


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


def _paged(path: str, params: dict, cap: int = 20) -> list[dict]:
    """次ページを cap 回まで辿る（点検なので取りすぎない）。"""
    out: list[dict] = []
    body = _get(path, params)
    out.extend(body.get("data") or [])
    nxt = (body.get("paging") or {}).get("next")
    while nxt and cap > 0:
        r = requests.get(nxt, timeout=TIMEOUT)
        if r.status_code >= 400:
            break
        body = r.json()
        out.extend(body.get("data") or [])
        nxt = (body.get("paging") or {}).get("next")
        cap -= 1
    return out


def accounts() -> list[dict]:
    fixed = [a.strip() for a in (os.environ.get("META_AD_ACCOUNTS") or "").split(",") if a.strip()]
    if fixed:
        return [{"id": a, "name": a} for a in fixed]
    return _paged("me/adaccounts", {"fields": "id,name", "limit": "200"})


def insights(act: str, since: str, until: str, unified: bool) -> list[dict]:
    p = {
        "level": "campaign",
        "time_increment": "1",
        "time_range": f'{{"since":"{since}","until":"{until}"}}',
        "fields": "date_start,campaign_id,campaign_name,spend,actions",
        "limit": "500",
    }
    if unified:
        p["use_unified_attribution_setting"] = "true"
    return _paged(f"{act}/insights", p)


def main() -> int:
    ap = argparse.ArgumentParser(description="Meta広告の actions を並べて見る")
    ap.add_argument("--since", default="2026-07-07", help="開始日 YYYY-MM-DD")
    ap.add_argument("--until", default="2026-07-13", help="終了日 YYYY-MM-DD")
    ap.add_argument("--campaign", default="", help="キャンペーン名にこの文字を含むものだけ詳しく出す")
    args = ap.parse_args()

    if not _token():
        print("ℹ️ META_ACCESS_TOKEN が未設定です。")
        print("   ads_read 権限つきのトークンを GitHub の Secrets に登録すると、")
        print("   ここで actions の中身を確かめられます。")
        return 0

    print("=" * 66)
    print(f"Meta広告 actions の点検  {args.since} 〜 {args.until} / API {API}")
    print("=" * 66)

    try:
        accts = accounts()
    except RuntimeError as e:
        print(f"❌ 広告アカウントが取れません: {e}")
        return 1
    print(f"\n広告アカウント: {len(accts)}件")

    for unified in (True, False):
        label = "広告セットの計測設定に従う（画面と同じはず）" if unified else "既定の計測設定"
        print("\n" + "=" * 66)
        print(f"■ {label}  use_unified_attribution_setting={'true' if unified else '(なし)'}")
        print("=" * 66)

        totals: dict[str, float] = defaultdict(float)
        seen_rows = 0
        detail: list[tuple] = []

        for a in accts:
            try:
                rows = insights(a["id"], args.since, args.until, unified)
            except RuntimeError as e:
                print(f"  ⚠️ {a.get('name') or a['id']}: {e}")
                continue
            for r in rows:
                seen_rows += 1
                for act in (r.get("actions") or []):
                    t = act.get("action_type") or "(不明)"
                    try:
                        totals[t] += float(act.get("value") or 0)
                    except (TypeError, ValueError):
                        pass
                if args.campaign and args.campaign in (r.get("campaign_name") or ""):
                    detail.append((r.get("date_start"), r.get("campaign_name"),
                                   r.get("spend"), r.get("actions") or []))

        print(f"\n  行数: {seen_rows}")
        print(f"\n  --- 返ってきた action_type と合計（多い順）---")
        if not totals:
            print("    （actions が1つも返っていません）")
        for t, v in sorted(totals.items(), key=lambda x: -x[1]):
            mark = ""
            low = t.lower()
            if "follow" in low:
                mark = "  ← フォロー候補"
            elif "profile" in low or "visit" in low:
                mark = "  ← プロフアクセス候補"
            print(f"    {t:<55} {v:>12,.0f}{mark}")

        if args.campaign:
            print(f"\n  --- 「{args.campaign}」を含むキャンペーンの日別 ---")
            if not detail:
                print("    （該当なし）")
            for d, nm, sp, acts in sorted(detail):
                print(f"    {d} {nm} 広告費={sp}")
                for act in sorted(acts, key=lambda x: str(x.get('action_type'))):
                    print(f"        {act.get('action_type'):<50} {act.get('value')}")

    print("\n" + "=" * 66)
    print("答え合わせ: Notimeつくば 2026-07-07 は")
    print("  広告費 1,800円 / プロフアクセス 135 / フォロー 36 が正解です。")
    print("  この数字に一致した action_type を、取り込みで使います。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
