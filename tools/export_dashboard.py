"""
ダッシュボード用データの集計と配信

Supabase の sales / visits を集計して data.json を作り、
ダッシュボードHTML(index.html) と一緒に Supabase Storage へアップロードする。

  python -m tools.export_dashboard              # 集計 → data.json/index.html を配信
  python -m tools.export_dashboard --print-json # data.json を標準出力にも出す

【2つのモード（環境変数 SUPABASE_ANON_KEY の有無で自動切替）】
  ● 公開モード（SUPABASE_ANON_KEY なし＝従来）
      公開バケット 'dashboard' に index.html と data.json を置く。
      URLを知っていれば誰でも見られる。
        {SUPABASE_URL}/storage/v1/object/public/dashboard/index.html
  ● 認証モード（SUPABASE_ANON_KEY あり）
      index.html は公開バケット 'dashboard'（URLは変わらない）。
      data.json は非公開バケット 'dashboard-data' に置き、
      Googleログイン（社内 @avend.co.jp のみ）した人だけが読める。
      HTMLにはログインに必要な公開情報(URL/anonキー)を埋め込む。
      ※ anonキーは公開して問題ない鍵。実データはRLSで保護される。
      公開バケット側の古い data.json は削除して漏れを防ぐ。

※ data.json は「日別×店舗」の素の合計だけを持ち、週次(月〜日)/月次への集約や
   比率(購入率・客単価など)の計算は、画面側(dashboard.html)で行う。
   ＝比率の平均という誤りを避け、どの粒度でも正しく出す。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.settings import JST, EtlError, load_dotenv  # noqa: E402
from etl.supabase_client import Supabase  # noqa: E402

BUCKET_PUBLIC = "dashboard"        # index.html（＋公開モードでは data.json も）
BUCKET_PRIVATE = "dashboard-data"  # 認証モードの data.json（RLSで保護）
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
def _ensure_bucket(sb: Supabase, bucket: str, public: bool) -> None:
    url = f"{sb.url}/storage/v1/bucket"
    resp = sb.session.post(url, data=json.dumps(
        {"id": bucket, "name": bucket, "public": public}), timeout=60)
    kind = "公開" if public else "非公開"
    if resp.status_code in (200, 201):
        print(f"  {kind}バケット '{bucket}' を作成しました")
    elif resp.status_code in (400, 409):
        print(f"  {kind}バケット '{bucket}' は既にあります")
    else:
        raise EtlError(f"バケット作成に失敗（HTTP {resp.status_code}）: {resp.text[:300]}")


def _upload(sb: Supabase, bucket: str, path: str, body: bytes,
            content_type: str, public: bool) -> str:
    import requests
    url = f"{sb.url}/storage/v1/object/{bucket}/{path}"
    # ⚠️ 生バイト(raw body)で送ると、こちらが Content-Type: text/html を指定しても
    #    Supabase 側が text/plain のまま保存してしまい、ブラウザが「ページ」ではなく
    #    「ソース文字列」を表示する不具合になる（実測で確認）。
    #    公式クライアント(storage-js)と同じ multipart/form-data で「ファイルの種類」を
    #    明示して送ると正しく text/html で保存・配信される。
    #    念のため一度消してから新規作成し、古い種類情報が残らないようにする。
    auth = {"apikey": sb.key, "Authorization": f"Bearer {sb.key}", "x-upsert": "true"}
    requests.delete(url, headers=auth, timeout=60)
    filename = path.split("/")[-1]
    resp = requests.post(
        url,
        headers=auth,
        data={"cacheControl": "0"},                        # キャッシュさせない
        files={"": (filename, body, content_type)},        # ← ファイルの種類を明示
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise EtlError(f"{path} のアップロードに失敗（HTTP {resp.status_code}）: {resp.text[:300]}")
    if public:
        loc = f"{sb.url}/storage/v1/object/public/{bucket}/{path}"
    else:
        loc = f"{bucket}/{path}（非公開・ログインした社内メンバーのみ）"
    print(f"  配信: {loc}")
    return loc


def _verify_served(public_url: str, cache_bust: str = "") -> None:
    """配信後、実際にブラウザが受け取るContent-Typeを確認して表示する。"""
    import requests
    for label, url in [("通常", public_url),
                       ("cache回避", f"{public_url}?cb={cache_bust}")]:
        try:
            r = requests.get(url, timeout=60)
            ct = r.headers.get("Content-Type")
            xcache = r.headers.get("x-cache") or r.headers.get("cf-cache-status") or "-"
            head = r.text[:40].replace("\n", " ")
            ok = "✅html" if (ct and "text/html" in ct) else "⚠️not-html"
            print(f"  検証[{label}]: HTTP {r.status_code} / CT={ct} / cache={xcache} / {ok} / 先頭『{head}』")
        except Exception as e:
            print(f"  検証[{label}]をスキップ（取得できず）: {e}")


def _delete(sb: Supabase, bucket: str, path: str) -> None:
    """公開バケットに残った古いファイルを消す（認証モードで漏れを防ぐ）。"""
    url = f"{sb.url}/storage/v1/object/{bucket}/{path}"
    resp = sb.session.delete(url, timeout=60)
    if resp.status_code in (200, 204):
        print(f"  削除: 公開側の {bucket}/{path} を消しました（非公開へ移行）")
    # 404（元々ない）等は無視でよい


def _inject_config(html: str, supa_url: str, anon: str, public_data_url: str) -> bytes:
    """HTMLのプレースホルダを実値に置き換える。"""
    return (html
            .replace("__SUPABASE_URL__", supa_url)
            .replace("__SUPABASE_ANON_KEY__", anon)
            .replace("__PUBLIC_DATA_URL__", public_data_url)
            ).encode("utf-8")


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

    anon = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    auth_mode = bool(anon)
    print(f"配信モード: {'認証（Googleログイン制・社内のみ）' if auth_mode else '公開（URLを知っていれば閲覧可）'}")

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
        html_text = (ROOT / "web" / "dashboard.html").read_text(encoding="utf-8")

        # index.html は常に公開バケット（URLを変えないため）
        _ensure_bucket(sb, BUCKET_PUBLIC, public=True)

        if auth_mode:
            # data.json は非公開バケットへ。HTMLに公開情報を埋め込む。
            _ensure_bucket(sb, BUCKET_PRIVATE, public=False)
            _upload(sb, BUCKET_PRIVATE, "data.json", data_bytes,
                    "application/json; charset=utf-8", public=False)
            html = _inject_config(html_text, sb.url, anon, "")
            _upload(sb, BUCKET_PUBLIC, "index.html", html,
                    "text/html; charset=utf-8", public=True)
            _delete(sb, BUCKET_PUBLIC, "data.json")  # 公開側の実データを消す
            page = f"{sb.url}/storage/v1/object/public/{BUCKET_PUBLIC}/index.html"
            _verify_served(page, cache_bust=data["generated_at"].replace(":",""))
            print(f"\n✅ 配信しました（認証モード）。社内の方は下のURLを開き、"
                  f"@avend.co.jp の Google でログインしてください。\n  {page}")
        else:
            # 従来どおり：公開バケットに両方置く
            html = _inject_config(html_text, "", "", "")
            _upload(sb, BUCKET_PUBLIC, "data.json", data_bytes,
                    "application/json; charset=utf-8", public=True)
            _upload(sb, BUCKET_PUBLIC, "index.html", html,
                    "text/html; charset=utf-8", public=True)
            page = f"{sb.url}/storage/v1/object/public/{BUCKET_PUBLIC}/index.html"
            _verify_served(page, cache_bust=data["generated_at"].replace(":",""))
            print(f"\n✅ 配信しました（公開モード）。ブラウザで下のURLを開いてください。\n  {page}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
