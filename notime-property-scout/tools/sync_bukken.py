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


def existing_ids(url: str, key: str) -> set[str]:
    """bukken の既存id一覧を取得（新規判定用）。"""
    endpoint = url.rstrip("/") + "/rest/v1/bukken?select=id"
    req = urllib.request.Request(endpoint, headers={
        "apikey": key, "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return {r["id"] for r in json.loads(resp.read().decode("utf-8"))}


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


def prune_placeholders(url: str, key: str) -> int:
    """未判定(verdict is null)の「個別URLでない行」「駐車場行」をDBから削除する。

    判定済み(OK/NG/保留)の行は絶対に消さない。プレースホルダ種や駐車場ノイズの掃除用。
    """
    from urllib.parse import quote
    base = url.rstrip("/") + "/rest/v1/bukken?"
    conds = [
        # 個別URLでない（detail/bukken を含まない）未判定行
        "verdict=is.null&detail_url=not.ilike.*detail*&detail_url=not.ilike.*bukken*",
        # 駐車場・月極 の未判定行
        "verdict=is.null&or=(name.ilike." + quote("*駐車場*") + ",name.ilike." + quote("*月極*") + ")",
        # 3階以上の未判定行（§2 G2：1階・2階のみ）
        "verdict=is.null&floor=gte.3",
        # 賃料が3万円未満の未判定行（坪単価/管理費の拾い間違い）
        "verdict=is.null&rent_yen=lt.30000",
    ]
    deleted = 0
    for q in conds:
        try:
            req = urllib.request.Request(base + q, method="DELETE", headers={
                "apikey": key, "Authorization": f"Bearer {key}",
                "Prefer": "return=representation",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                deleted += len(json.loads(resp.read().decode("utf-8") or "[]"))
        except Exception as e:
            print(f"  prune警告: {e}")
    return deleted


def _emit_output(new: list[dict], total: int) -> None:
    """GitHub Actions 用の出力（new_count / names / summary）を書き出す。"""
    gh = os.environ.get("GITHUB_OUTPUT")
    if not gh:
        return
    names = " / ".join(f"{r.get('city','')}{r.get('name','')}" for r in new[:8])
    summary = (f"{total}件同期（うち新規{len(new)}件）"
               + (f"：{names}" if new else ""))
    with open(gh, "a", encoding="utf-8") as f:
        f.write(f"new_count={len(new)}\n")
        f.write(f"names={names}\n")
        f.write(f"summary={summary}\n")


def main() -> int:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY が未設定です", file=sys.stderr)
        return 2
    rows = load_feed()
    if not rows:
        print("feed が空です。何もしません。")
        _emit_output([], 0)
        return 0
    before = existing_ids(url, key)
    new = [r for r in rows if r["id"] not in before]
    upsert(rows, url, key)
    pruned = prune_placeholders(url, key)
    print(f"bukken に {len(rows)} 件 upsert（新規 {len(new)} 件、verdict/reason は保持）。掃除 {pruned} 件。")
    for r in new:
        print(f"  + {r.get('city','')} {r.get('name','')}")
    _emit_output(new, len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
