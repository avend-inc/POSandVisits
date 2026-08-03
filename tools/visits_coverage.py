"""
来店客数（デジテール）の“取りこぼし”横展開チェック

目的:
  「SELFURUGI徳島沖浜店の来店客数が入っていない」の原因究明と横展開。
  ある月について、売上はあるのに来店客数(visits, source=digitel)が
  1件も無い店を洗い出す。さらに各店が
    ・digitel_slug を持っているか（＝デジテールに紐づいているか）
    ・kpi_visitors（来店をKPIに使う設定）が True か
  を並べて、原因の切り分けを一目で分かるようにする。

使い方:
  python -m tools.visits_coverage 2026-07     # 対象月（YYYY-MM）
  python -m tools.visits_coverage             # 省略時は当月

出るもの（stdout）:
  1) 売上あり × 来店ゼロ の店一覧（＝来店が入っていない店）
       - slug無し → デジテール未連携 or 名前がブランド始まりでなく discover で
                     取りこぼし（digitel_probe で要確認）
       - slugあり → 連携済みなのに来店ゼロ → その月のCSVが空／店ID割れ の疑い
  2) 参考: 全店の 売上有無 / 来店有無 / slug / kpi_visitors 一覧
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.settings import load_dotenv  # noqa: E402
from etl.supabase_client import Supabase  # noqa: E402

PAGE = 1000


def _month_span(ym: str) -> tuple[str, str]:
    y, m = int(ym[:4]), int(ym[5:7])
    start = f"{y:04d}-{m:02d}-01"
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = f"{ny:04d}-{nm:02d}-01"  # exclusive
    return start, end


def _store_ids(sb: Supabase, table: str, start: str, end: str,
               extra: dict | None = None) -> set[int]:
    """その月にその表へ行がある store_id の集合（ページ制限を超えて全件）。"""
    ids: set[int] = set()
    offset = 0
    while True:
        # PostgREST は同名パラメータ(business_date)を2回書けないので、
        # 期間の下限・上限は and=(...) にまとめて渡す。
        req = {
            "select": "store_id",
            "and": f"(business_date.gte.{start},business_date.lt.{end})",
            "order": "store_id",
            "limit": str(PAGE), "offset": str(offset),
        }
        if extra:
            req.update(extra)
        chunk = sb.select(table, req)
        for r in chunk:
            if r.get("store_id") is not None:
                ids.add(int(r["store_id"]))
        if len(chunk) < PAGE:
            break
        offset += PAGE
    return ids


def main() -> int:
    load_dotenv()
    ym = sys.argv[1] if len(sys.argv) > 1 else None
    if not ym:
        # 引数省略時は当月。Date は使わず SUPABASE から max(business_date) を拾う。
        sb0 = Supabase()
        latest = sb0.select("sales", {"select": "business_date",
                                      "order": "business_date.desc", "limit": "1"})
        ym = (latest[0]["business_date"][:7] if latest else "2026-01")
        sb = sb0
    else:
        sb = Supabase()

    start, end = _month_span(ym)
    print(f"===== 来店客数カバレッジ点検  対象月: {ym}（{start} 〜 {end} 未満）=====")

    stores = sb.select("stores", {"select": "*", "order": "id"})
    name_by_id = {s["id"]: s["name"] for s in stores}
    slug_by_id = {s["id"]: (s.get("digitel_slug") or "") for s in stores}
    own_by_id = {s["id"]: (s.get("ownership") or "直営") for s in stores}
    kv_by_id = {s["id"]: bool(s.get("kpi_visitors", True)) for s in stores}
    vis_by_id = {s["id"]: bool(s.get("visible", True)) for s in stores}

    sales_ids = _store_ids(sb, "sales", start, end)
    visit_ids = _store_ids(sb, "visits", start, end, extra={"source": "eq.digitel"})

    missing = sorted(sales_ids - visit_ids, key=lambda i: name_by_id.get(i, ""))
    print(f"\n----- ① 売上あり × 来店ゼロ の店（{len(missing)}件）-----")
    if not missing:
        print("  なし（売上のある全店で来店データが入っています）")
    for sid in missing:
        slug = slug_by_id.get(sid, "")
        tag = "slug無し→デジテール未連携/discover取りこぼし" if not slug \
            else "slugあり→連携済みなのに来店ゼロ(CSV空/店ID割れの疑い)"
        kv = "" if kv_by_id.get(sid, True) else " ⚠kpi_visitors=false"
        print(f"  ・{name_by_id.get(sid, sid)}（{own_by_id.get(sid,'?')}）"
              f" slug={slug or '—'} … {tag}{kv}")

    print(f"\n----- ② 参考: 全店カバレッジ一覧 -----")
    print("  店名 / 区分 / 売上 / 来店 / slug / kpi_visitors / 表示")
    for sid in sorted(name_by_id, key=lambda i: name_by_id[i]):
        has_s = "○" if sid in sales_ids else "—"
        has_v = "○" if sid in visit_ids else "—"
        print(f"  {name_by_id[sid]} / {own_by_id.get(sid,'?')} / "
              f"売上{has_s} / 来店{has_v} / "
              f"slug={slug_by_id.get(sid) or '—'} / "
              f"kpi_visitors={kv_by_id.get(sid, True)} / "
              f"表示={vis_by_id.get(sid, True)}")

    print("\n===== 点検おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
