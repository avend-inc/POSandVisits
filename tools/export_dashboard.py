"""
ダッシュボード用データの集計と配信

Supabase の sales / visits を集計して data.json を作り、
ダッシュボードHTML(index.html) と一緒に Supabase Storage の公開バケット
「dashboard」へアップロードする。

  python -m tools.export_dashboard              # 集計 → data.json/index.html を配信
  python -m tools.export_dashboard --print-json # data.json を標準出力にも出す

配信後の公開URL:
  {SUPABASE_URL}/storage/v1/object/public/dashboard/index.html
  {SUPABASE_URL}/storage/v1/object/public/dashboard/data.json

※ data.json は「日別×店舗」の素の合計だけを持ち、週次(月〜日)/月次への集約や
   比率(購入率・客単価など)の計算は、画面側(dashboard.html)で行う。
   ＝比率の平均という誤りを避け、どの粒度でも正しく出す。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.settings import JST, EtlError, load_dotenv  # noqa: E402
from etl.supabase_client import Supabase  # noqa: E402

BUCKET = "dashboard"
PAGE = 1000


def _select_all(sb: Supabase, table: str, select: str,
                order: str, extra: dict | None = None) -> list[dict]:
    """PostgREST のページ制限(1000件)を超えて全件取る。"""
    rows: list[dict] = []
    offset = 0
    while True:
        params = {"select": select, "order": order,
                  "limit": str(PAGE), "offset": str(offset)}
        if extra:
            params.update(extra)
        chunk = sb.select(table, params)
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        offset += PAGE
    return rows


def build_data(sb: Supabase) -> dict:
    stores = sb.select("stores", {"select": "id,name", "order": "id"})
    name_by_id = {s["id"]: s["name"] for s in stores}

    # --- sales: 伝票(税込/税抜/点数)は明細行に繰り返し入っているので、
    #     伝票単位では tx_id ごとに1回だけ数える。明細(金額/点数)は行ごとに合計。
    sales = _select_all(
        sb, "sales",
        "business_date,store_id,tx_id,sales_in_tax,sales_ex_tax,tx_qty,"
        "line_category,line_amount,line_qty",
        order="id",
    )

    tx_seen: set[tuple] = set()
    daily: dict[tuple, dict] = {}     # (date, store_id) -> 伝票合計
    cat: dict[tuple, dict] = {}       # (date, store_id, category) -> 明細合計

    def _num(v):
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    for r in sales:
        d = r["business_date"]
        sid = r["store_id"]
        dk = (d, sid)
        rec = daily.setdefault(dk, {"in": 0.0, "ex": 0.0, "tx": 0, "it": 0.0, "v": None})

        txkey = (sid, r["tx_id"])
        if txkey not in tx_seen:
            tx_seen.add(txkey)
            rec["in"] += _num(r["sales_in_tax"])
            rec["ex"] += _num(r["sales_ex_tax"])
            rec["it"] += _num(r["tx_qty"])
            rec["tx"] += 1

        c = (r.get("line_category") or "その他").strip() or "その他"
        ck = (d, sid, c)
        crec = cat.setdefault(ck, {"a": 0.0, "q": 0.0})
        crec["a"] += _num(r["line_amount"])
        crec["q"] += _num(r["line_qty"])

    # --- visits: 来店客数を日別×店舗に載せる
    visits = _select_all(
        sb, "visits",
        "business_date,store_id,visitors",
        order="id", extra={"source": "eq.digitel"},
    )
    for r in visits:
        dk = (r["business_date"], r["store_id"])
        rec = daily.setdefault(dk, {"in": 0.0, "ex": 0.0, "tx": 0, "it": 0.0, "v": None})
        rec["v"] = (rec["v"] or 0) + int(r["visitors"] or 0)

    daily_rows = [
        {"d": d, "s": sid,
         "in": round(v["in"]), "ex": round(v["ex"]),
         "tx": v["tx"], "it": round(v["it"]),
         "v": v["v"]}
        for (d, sid), v in sorted(daily.items())
    ]
    cat_rows = [
        {"d": d, "s": sid, "c": c, "a": round(v["a"]), "q": round(v["q"])}
        for (d, sid, c), v in sorted(cat.items())
    ]

    return {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "stores": [{"id": s["id"], "name": name_by_id.get(s["id"], str(s["id"]))}
                   for s in stores],
        "daily": daily_rows,
        "cat": cat_rows,
    }


# ------------------------------------------------------------
# Supabase Storage への配信
# ------------------------------------------------------------
def _ensure_bucket(sb: Supabase) -> None:
    url = f"{sb.url}/storage/v1/bucket"
    resp = sb.session.post(url, data=json.dumps(
        {"id": BUCKET, "name": BUCKET, "public": True}), timeout=60)
    if resp.status_code in (200, 201):
        print(f"  公開バケット '{BUCKET}' を作成しました")
    elif resp.status_code in (400, 409):
        print(f"  公開バケット '{BUCKET}' は既にあります")
    else:
        raise EtlError(f"バケット作成に失敗（HTTP {resp.status_code}）: {resp.text[:300]}")


def _upload(sb: Supabase, path: str, body: bytes, content_type: str) -> str:
    url = f"{sb.url}/storage/v1/object/{BUCKET}/{path}"
    resp = sb.session.post(
        url, data=body,
        headers={
            "Content-Type": content_type,
            "x-upsert": "true",
            # 毎回最新を配信するためキャッシュさせない（更新が即反映されるように）
            "cache-control": "no-cache, max-age=0",
        },
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise EtlError(f"{path} のアップロードに失敗（HTTP {resp.status_code}）: {resp.text[:300]}")
    public = f"{sb.url}/storage/v1/object/public/{BUCKET}/{path}"
    print(f"  配信: {public}")
    return public


def main() -> int:
    parser = argparse.ArgumentParser(description="ダッシュボードデータの集計と配信")
    parser.add_argument("--print-json", action="store_true",
                        help="data.json の中身を標準出力にも出す")
    parser.add_argument("--no-deploy", action="store_true",
                        help="Storageへの配信をせず集計だけ行う")
    args = parser.parse_args()

    load_dotenv()
    try:
        sb = Supabase()
    except EtlError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    print("ダッシュボード用データを集計しています...")
    data = build_data(sb)
    print(f"  日別×店舗: {len(data['daily'])}件 / カテゴリ日別: {len(data['cat'])}件")
    data_bytes = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    print(f"  data.json サイズ: {len(data_bytes)//1024} KB")

    if args.print_json:
        print("----DATA_JSON_BEGIN----")
        print(data_bytes.decode("utf-8"))
        print("----DATA_JSON_END----")

    if not args.no_deploy:
        html_path = ROOT / "web" / "dashboard.html"
        html = html_path.read_bytes()
        _ensure_bucket(sb)
        _upload(sb, "data.json", data_bytes, "application/json; charset=utf-8")
        _upload(sb, "index.html", html, "text/html; charset=utf-8")
        print("\n✅ 配信しました。ブラウザで下の index.html を開いてください。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
