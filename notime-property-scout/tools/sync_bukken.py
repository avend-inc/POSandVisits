"""feeds/bukken.jsonl の物件を Supabase の bukken テーブルへ upsert する。

この環境（Claudeセッション）からは Supabase に届かないため、書き込みは
GitHub Actions（SUPABASE_SERVICE_KEY を持つ）で実行する前提のスクリプト。

方針:
  - id で upsert（重複は更新）。物件スペック列だけ送るので、ユーザーが付けた
    verdict / reason / reviewed_* は上書きしない（PostgREST は送った列だけ更新する）。
  - 依存を増やさないよう標準ライブラリ(urllib)のみ。

環境変数:
  SUPABASE_URL          例 https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  service_role キー（RLSを越えて書ける・秘匿）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

FEED = Path(__file__).resolve().parent.parent / "feeds" / "bukken.jsonl"

# bukken テーブルの「物件側」列だけを送る（判定列は触らない）。
PROP_COLS = ["id", "city", "name", "address", "area_tsubo", "area_sqm",
             "rent_yen", "parking", "floor", "source", "detail_url",
             "success_flag", "spec_note", "note", "first_seen"]


def load_feed(path: Path = FEED) -> list[dict]:
    rows, seen = [], {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        rec = {k: obj[k] for k in PROP_COLS if k in obj}
        if "id" not in rec:
            raise ValueError(f"id がありません: {line[:80]}")
        seen[rec["id"]] = rec   # 同一idは後勝ち（最新の追記を採用）
    rows = list(seen.values())
    return rows


def upsert(rows: list[dict], url: str, key: str) -> None:
    endpoint = url.rstrip("/") + "/rest/v1/bukken?on_conflict=id"
    body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # merge-duplicates: 既存行は送った列だけ更新（verdict/reason は保持）
        "Prefer": "resolution=merge-duplicates,return=minimal",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        if resp.status not in (200, 201, 204):
            raise RuntimeError(f"upsert失敗 HTTP {resp.status}: {resp.read()[:300]!r}")


def main() -> int:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY が未設定です", file=sys.stderr)
        return 2
    rows = load_feed()
    if not rows:
        print("feed が空です。何もしません。")
        return 0
    upsert(rows, url, key)
    print(f"bukken に {len(rows)} 件 upsert しました（verdict/reason は保持）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
