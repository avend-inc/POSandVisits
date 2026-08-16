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
import urllib.error
import urllib.request
from pathlib import Path

FEED = Path(__file__).resolve().parent.parent / "feeds" / "bukken.jsonl"

# bukken テーブルの「物件側」列だけを送る（判定列は触らない）。
PROP_COLS = ["id", "city", "name", "address", "area_tsubo", "area_sqm",
             "rent_yen", "parking", "floor", "station_name", "usage", "source", "detail_url",
             "success_flag", "spec_note", "note", "first_seen"]
# 後から追加した任意列（DBに未追加でも家賃等は反映させたいので、失敗時はこれらを落として再試行）
OPTIONAL_COLS = ["station_name", "usage"]


def load_feed(path: Path = FEED) -> list[dict]:
    raw = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        if "id" not in obj:
            raise ValueError(f"id がありません: {line[:80]}")
        raw.append(obj)
    # PostgREST の一括upsertは「全オブジェクトのキー集合が一致」必須(PGRST102)。
    # 送る列 = PROP_COLS のうち feed のどれかに実在した列だけに絞り（存在しないDB列を送らない）、
    # 全行をその列でそろえる（無い行は None 埋め）。これで usage/station_name も欠けず送れる。
    present = [c for c in PROP_COLS if any(c in o for o in raw)]
    seen = {}
    for obj in raw:
        rec = {k: obj.get(k) for k in present}
        seen[rec["id"]] = rec   # 同一idは後勝ち（最新の追記を採用）
    return list(seen.values())


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
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status not in (200, 201, 204):
                raise RuntimeError(f"upsert失敗 HTTP {resp.status}: {resp.read()[:400]!r}")
    except urllib.error.HTTPError as e:
        # PostgREST の本当のエラー本文（どの列/制約が原因か）を見えるようにする
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail[:400]}") from None


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
        # 面積が判明して25〜50坪レンジ外の未判定行（狭い/広い＝条件不一致が確定）。
        #  ※ 面積不明(null)は比較が真にならないので残る（＝確認で変わりうる物件だけ残す）。
        "verdict=is.null&area_tsubo=gt.50",
        "verdict=is.null&area_tsubo=lt.25",
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


def prune_not_in_feed(url: str, key: str, feed_ids: set[str]) -> int:
    """feed に無い「未判定(verdict is null)」行をDBから削除する。

    用途スクリーニングや面積上限で feed から除外した行を、レビュー画面(DB)からも消すため。
    判定済み(OK/NG/保留)は feed に無くても残す（ユーザーの判定を失わない）。
    """
    from urllib.parse import quote
    base = url.rstrip("/") + "/rest/v1/bukken"
    # 未判定の既存idを取得
    req = urllib.request.Request(base + "?select=id&verdict=is.null", headers={
        "apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            db_ids = [r["id"] for r in json.loads(resp.read().decode("utf-8"))]
    except Exception as e:
        print(f"  prune_not_in_feed警告(取得): {e}")
        return 0
    stale = [i for i in db_ids if i not in feed_ids]
    deleted = 0
    for i in range(0, len(stale), 80):        # URL長対策でチャンク削除
        chunk = stale[i:i + 80]
        inlist = ",".join(quote(x) for x in chunk)
        q = f"?verdict=is.null&id=in.({inlist})"
        try:
            req = urllib.request.Request(base + q, method="DELETE", headers={
                "apikey": key, "Authorization": f"Bearer {key}",
                "Prefer": "return=representation"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                deleted += len(json.loads(resp.read().decode("utf-8") or "[]"))
        except Exception as e:
            print(f"  prune_not_in_feed警告(削除): {e}")
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
    try:
        upsert(rows, url, key)
    except Exception as e:
        # station_name / usage 列が未追加等 → それらを落として再試行（家賃等は必ず反映させる）
        print(f"upsert再試行（列不足の可能性）: {e}", file=sys.stderr)
        rows2 = [{k: v for k, v in r.items() if k not in OPTIONAL_COLS} for r in rows]
        upsert(rows2, url, key)
    feed_ids = {r["id"] for r in rows}
    removed = prune_not_in_feed(url, key, feed_ids)   # 除外(事務所/100坪超等)された未判定行をDBからも削除
    pruned = prune_placeholders(url, key)
    print(f"bukken に {len(rows)} 件 upsert（新規 {len(new)} 件、verdict/reason は保持）。"
          f"feed外削除 {removed} 件・掃除 {pruned} 件。")
    for r in new:
        print(f"  + {r.get('city','')} {r.get('name','')}")
    _emit_output(new, len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
