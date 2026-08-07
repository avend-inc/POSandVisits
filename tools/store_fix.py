"""店舗の表示フラグ調整＋現行data.jsonの店舗リスト確認（長野店の非表示など）。

・現在配信中の data.json（非公開バケット）の stores 名一覧を出す
   → 「NOTIMEいわき（2026/4/25~2026/4/28）」等の幽霊がまだ居るか＝キャッシュか本物かを判定。
・APPLY=1 のとき、HIDE_IDS（既定 "50"）の店を visible=false にする（開店前の長野を経営ビューから隠す）。
   visible は私たちが復元時に足した列で、在庫アプリは使っていない（他アプリ無影響）。データは消さない。

  実行: python -m tools.store_fix          … 確認のみ（無変更）
        APPLY=1 python -m tools.store_fix  … HIDE_IDS を非表示に更新
"""
from __future__ import annotations
import json
import os
import re

import requests

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
SVC = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
APPLY = os.environ.get("APPLY") == "1"
HIDE_IDS = [x.strip() for x in (os.environ.get("HIDE_IDS") or "50").split(",") if x.strip()]


def run_sql(sql: str):
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": sql}, timeout=60)
    if r.status_code >= 400:
        raise SystemExit(f"SQL失敗 HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def main() -> int:
    # 1) 現行 data.json の店舗一覧（＝フロントの選択肢が読む元）
    r = requests.get(f"{URL}/storage/v1/object/dashboard-data/data.json",
                     headers={"apikey": SVC, "Authorization": f"Bearer {SVC}"}, timeout=120)
    r.raise_for_status()
    data = r.json()
    stores = data.get("stores") or []
    print(f"現行 data.json stores: {len(stores)}店 / generated_at={data.get('generated_at')}")
    n_noid = 0
    for s in stores:
        flag = "" if s.get("visible", True) else "  [非表示]"
        susp = "  ← 怪しい" if re.search(r"[（(〜~]|[0-9]{4}", s.get("name", "") or "") else ""
        sid = s.get("id")
        if sid is None:
            n_noid += 1
        print(f"  id={str(sid):>4} {s.get('name')}{flag}{susp}")
    print(f"  （うち id=None の行: {n_noid}）")

    # 2) 長野など HIDE_IDS の現状
    ids = ",".join(str(int(x)) for x in HIDE_IDS)
    print(f"\nHIDE_IDS = {HIDE_IDS}")
    print(json.dumps(run_sql(
        f"select id,name,visible from public.stores where id in ({ids}) order by id"),
        ensure_ascii=False))

    if not APPLY:
        print("\n【確認のみ】APPLY=1 で HIDE_IDS を visible=false にします（今回は無変更）。")
        return 0

    print("\n【適用】visible=false に更新します…")
    run_sql(f"update public.stores set visible=false where id in ({ids});")
    print("✅ 更新完了。事後確認:")
    print(json.dumps(run_sql(
        f"select id,name,visible from public.stores where id in ({ids}) order by id"),
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
