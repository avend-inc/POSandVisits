"""
このSupabaseプロジェクトに「どんなテーブルがあるか」を一覧する（読み取りのみ）。

    python -m tools.tables_probe            # 全テーブル
    python -m tools.tables_probe meta ad    # 名前に meta / ad を含むものだけ詳しく出す

【なぜ必要か】
  このプロジェクトには複数のアプリ（売上分析・在庫管理・事業計画）が同居していて、
  リポジトリに定義が無いテーブルも入っている。新しくダッシュボードを作れるか
  判断するには、まず「そのデータが本当にDBにあるか」を確かめる必要がある。

  読み取り専用。テーブルの中身は出さず、名前・列・件数だけを見る。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""


def run(sql: str):
    if not REF or not TOKEN:
        raise SystemExit("SUPABASE_URL / SUPABASE_ACCESS_TOKEN が設定されていません。")
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": sql}, timeout=60)
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def main() -> int:
    words = [w.strip().lower() for w in sys.argv[1:] if w.strip()]

    rows = run("""
        select c.relname as table_name,
               c.reltuples::bigint as approx_rows
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public' and c.relkind in ('r','p')
         order by c.relname
    """)
    print(f"===== public のテーブル一覧（{len(rows)}件）=====")
    print("  ※ 件数は統計情報からの概算です（ANALYZE前は -1 や 0 になります）")
    for r in rows:
        print(f"  {r['table_name']:<34} 約{r['approx_rows']:>10,} 行")

    if not words:
        print("\n（キーワードを渡すと、その名前を含むテーブルの列も出します）")
        return 0

    hit = [r["table_name"] for r in rows
           if any(w in r["table_name"].lower() for w in words)]
    print(f"\n===== キーワード {words} に一致したテーブル（{len(hit)}件）=====")
    if not hit:
        print("  該当なし。")
        return 0

    for t in hit:
        cols = run(f"""
            select column_name, data_type
              from information_schema.columns
             where table_schema='public' and table_name='{t}'
             order by ordinal_position
        """)
        cnt = run(f"select count(*) as n from public.{t}")
        n = cnt[0]["n"] if cnt else "?"
        print(f"\n--- {t}（実件数 {n}）---")
        for c in cols:
            print(f"    {c['column_name']:<28} {c['data_type']}")
        # 期間が分かる列があれば、データの入っている範囲も出す（中身は出さない）
        names = [c["column_name"] for c in cols]
        for dcol in ("date", "date_start", "day", "business_date", "ymd", "ym", "month"):
            if dcol in names:
                rng = run(f"select min({dcol})::text as lo, max({dcol})::text as hi from public.{t}")
                if rng:
                    print(f"    → {dcol} の範囲: {rng[0]['lo']} 〜 {rng[0]['hi']}")
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
