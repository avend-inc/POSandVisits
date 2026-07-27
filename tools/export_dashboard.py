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

# ダッシュボードHTMLは GitHub Pages が配信する（Supabaseはブラウザ向けHTMLを
# text/plain でしか返せず「ソース表示」になるため）。Supabase には data.json だけ
# 置き、GitHub Pages のページがそれを読み込む。data.json は fet().json() で読むので
# Content-Type は問題にならない。日次ETLはこの 'dashboard' バケットを更新する。
BUCKET_PUBLIC = "dashboard"        # data.json（＋互換のため index.html も置く）
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
    # "*" で全列取得。ownership 列が未追加でも動くよう .get で既定「直営」にする。
    stores = sb.select("stores", {"select": "*", "order": "id"})
    name_by_id = {s["id"]: s["name"] for s in stores}
    own_by_id = {s["id"]: (s.get("ownership") or "直営") for s in stores}

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
    catprice: dict[tuple, dict] = {}  # (date, store_id, category, 単価) -> 明細合計（価格帯別）

    # 商品ではないので「売上・点数・カテゴリ」すべてから除外する。
    #  ・レジ袋 / クーポン … 物ではない（config.py の NON_MERCH と同じ考え方）
    #  ・「不明」は EZレジで明細が無いだけの“実売上”なので売上には残す（除外しない）
    EXCLUDE = {"レジ袋", "クーポン"}

    def _new() -> dict:
        # in/ex=伝票の税込/税抜合計、tx=伝票数、it=販売点数(レジ袋等を除く)、
        # bag_in/bag_ex=レジ袋等の税込/税抜ぶん（KPIからは除外し、別枠で表示する）、
        # bag_q=レジ袋・クーポンの点数
        return {"in": 0.0, "ex": 0.0, "tx": 0, "it": 0.0, "v": None,
                "bag_in": 0.0, "bag_ex": 0.0, "bag_q": 0.0, "reji_in": 0.0}

    def _num(v):
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    for r in sales:
        d = r["business_date"]
        sid = r["store_id"]
        dk = (d, sid)
        rec = daily.setdefault(dk, _new())

        ex_tax = _num(r["sales_ex_tax"])
        in_tax = _num(r["sales_in_tax"])

        # 伝票単位の値（税込/税抜合計・伝票数）は tx_id ごとに1回だけ
        txkey = (sid, r["tx_id"])
        if txkey not in tx_seen:
            tx_seen.add(txkey)
            rec["in"] += in_tax
            rec["ex"] += ex_tax
            rec["tx"] += 1
            # 点数は tx_qty ではなく明細(line_qty)の合計で数える（下で加算）。
            # こうするとレジ袋・クーポンを点数から自然に除ける。

        c = (r.get("line_category") or "その他").strip() or "その他"
        amt = _num(r["line_amount"])
        qty = _num(r["line_qty"])

        if c in EXCLUDE:
            # レジ袋・クーポンぶんを控えておき、売上から差し引く。
            # 点数にもカテゴリにも入れない。
            rec["bag_ex"] += amt
            ratio = (in_tax / ex_tax) if ex_tax else 1.0   # 伝票の税率で税込換算
            rec["bag_in"] += amt * ratio
            rec["bag_q"] += qty
            if c == "レジ袋":                 # レジ袋売上は単体で（クーポン割引と混ぜない）
                rec["reji_in"] += amt * ratio
            continue

        rec["it"] += qty                       # 販売点数（レジ袋・クーポンを除く）
        ck = (d, sid, c)
        crec = cat.setdefault(ck, {"a": 0.0, "q": 0.0})
        crec["a"] += amt
        crec["q"] += qty

        # 価格帯別（カテゴリ内の単価ごと）。単価＝明細金額÷点数を最寄りの円に丸める。
        if qty > 0 and amt > 0:
            price = int(round(amt / qty))
            pk = (d, sid, c, price)
            prec = catprice.setdefault(pk, {"a": 0.0, "q": 0.0})
            prec["a"] += amt
            prec["q"] += qty

    # --- visits: 来店客数を日別×店舗に載せる
    visits = _select_all(
        sb, "visits",
        "business_date,store_id,visitors",
        order="id", extra={"source": "eq.digitel"},
    )
    for r in visits:
        dk = (r["business_date"], r["store_id"])
        rec = daily.setdefault(dk, _new())
        rec["v"] = (rec["v"] or 0) + int(r["visitors"] or 0)

    daily_rows = [
        {"d": d, "s": sid,
         # 税込/税抜売上からレジ袋・クーポンぶんを差し引く（マイナスにはしない）
         "in": round(max(v["in"] - v["bag_in"], 0)),
         "ex": round(max(v["ex"] - v["bag_ex"], 0)),
         "tx": v["tx"], "it": round(v["it"]),
         "v": v["v"],
         "bag": round(v["reji_in"]), "bagq": round(v["bag_q"])}
        for (d, sid), v in sorted(daily.items())
    ]
    cat_rows = [
        {"d": d, "s": sid, "c": c, "a": round(v["a"]), "q": round(v["q"])}
        for (d, sid, c), v in sorted(cat.items())
    ]
    catprice_rows = [
        {"d": d, "s": sid, "c": c, "p": p, "a": round(v["a"]), "q": round(v["q"])}
        for (d, sid, c, p), v in sorted(catprice.items())
    ]

    return {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "stores": [{"id": s["id"], "name": name_by_id.get(s["id"], str(s["id"])),
                    "own": own_by_id.get(s["id"], "直営")}
                   for s in stores],
        "daily": daily_rows,
        "cat": cat_rows,
        "catp": catprice_rows,
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
    dresp = requests.delete(url, headers={"apikey": sb.key,
                            "Authorization": f"Bearer {sb.key}"}, timeout=60)
    print(f"  （削除: HTTP {dresp.status_code} {dresp.text[:80]}）")
    filename = path.split("/")[-1]
    # 公式クライアント(storage-js/storage3)と同じ形にする：
    #   ・cache-control は「ヘッダ」で渡す（フォーム項目にしない）
    #   ・本文はファイル1つだけの multipart（種類を明示）
    # こうしないと保存メタデータの mimetype が空になり、配信が text/plain になる。
    resp = requests.post(
        url,
        headers={"apikey": sb.key, "Authorization": f"Bearer {sb.key}",
                 "x-upsert": "true", "cache-control": "max-age=0"},
        files={"file": (filename, body, content_type)},    # ← ファイルの種類を明示
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


def _verify_served(sb: Supabase, bucket: str, path: str,
                   public_url: str, cache_bust: str = "") -> None:
    """配信後、実際に配信されるContent-Typeと、保存されている本当の種類を確認する。"""
    import requests
    # ① ブラウザが受け取る種類（公開URL＝CDN経由）
    for label, url in [("通常", public_url),
                       ("cache回避", f"{public_url}?cb={cache_bust}")]:
        try:
            r = requests.get(url, timeout=60)
            ct = r.headers.get("Content-Type")
            age = r.headers.get("age") or "-"
            cc = r.headers.get("cache-control") or "-"
            ok = "✅html" if (ct and "text/html" in ct) else "⚠️not-html"
            print(f"  検証[{label}]: HTTP {r.status_code} / CT={ct} / age={age} / cc={cc} / {ok}")
        except Exception as e:
            print(f"  検証[{label}]をスキップ: {e}")
    # ② 保存されている本当の種類（infoエンドポイント＝CDNを通さない原本）
    try:
        info_url = f"{sb.url}/storage/v1/object/info/public/{bucket}/{path}"
        r = requests.get(info_url, headers={"apikey": sb.key,
                         "Authorization": f"Bearer {sb.key}"}, timeout=60)
        print(f"  保存種類[info]: HTTP {r.status_code} / {r.text[:500]}")
    except Exception as e:
        print(f"  保存種類[info]をスキップ: {e}")
    # ③ 原本の配信種類（認証ダウンロード＝公開CDNを通さない）
    try:
        au = f"{sb.url}/storage/v1/object/authenticated/{bucket}/{path}"
        r = requests.get(au, headers={"apikey": sb.key,
                         "Authorization": f"Bearer {sb.key}"}, timeout=60)
        print(f"  原本配信[auth]: HTTP {r.status_code} / CT={r.headers.get('Content-Type')}")
    except Exception as e:
        print(f"  原本配信[auth]をスキップ: {e}")


def _delete(sb: Supabase, bucket: str, path: str) -> None:
    """公開バケットに残った古いファイルを確実に消す（認証モードで漏れを防ぐ）。
    単体DELETEと一括removeの両方を叩き、実ステータスをログに出す（空振り検知のため）。"""
    # ① 単体 DELETE
    url = f"{sb.url}/storage/v1/object/{bucket}/{path}"
    try:
        resp = sb.session.delete(url, timeout=60)
        print(f"  公開側 {bucket}/{path} 削除(単体): HTTP {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        print(f"  公開側 単体削除スキップ: {e}")
    # ② 一括 remove（storage3 の remove 相当。prefixes 指定・保険）
    try:
        r2 = sb.session.request(
            "DELETE", f"{sb.url}/storage/v1/object/{bucket}",
            data=json.dumps({"prefixes": [path]}), timeout=60)
        print(f"  公開側 {bucket}/{path} 削除(一括): HTTP {r2.status_code} {r2.text[:120]}")
    except Exception as e:
        print(f"  公開側 一括削除スキップ: {e}")


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
    print(f"  日別×店舗: {len(data['daily'])}件 / カテゴリ日別: {len(data['cat'])}件 / 価格帯別: {len(data.get('catp',[]))}件")
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
            # 画面が読む公開設定（anon は公開してよい鍵。画面はこれを見て認証モードで動く）
            cfg = json.dumps({"mode": "auth", "url": sb.url, "anon": anon},
                             ensure_ascii=False).encode("utf-8")
            _upload(sb, BUCKET_PUBLIC, "config.json", cfg,
                    "application/json; charset=utf-8", public=True)
            page = f"{sb.url}/storage/v1/object/public/{BUCKET_PUBLIC}/index.html"
            _verify_served(sb, BUCKET_PUBLIC, "index.html", page, cache_bust=data["generated_at"].replace(":",""))
            print(f"\n✅ 配信しました（認証モード）。社内の方は下のURLを開き、"
                  f"@avend.co.jp の Google でログインしてください。\n  {page}")
        else:
            # 従来どおり：公開バケットに両方置く
            html = _inject_config(html_text, "", "", "")
            _upload(sb, BUCKET_PUBLIC, "data.json", data_bytes,
                    "application/json; charset=utf-8", public=True)
            _upload(sb, BUCKET_PUBLIC, "index.html", html,
                    "text/html; charset=utf-8", public=True)
            # 画面が読む公開設定（公開モード：data.json の公開URLを指す）
            cfg = json.dumps({"mode": "public",
                              "dataUrl": f"{sb.url}/storage/v1/object/public/{BUCKET_PUBLIC}/data.json"},
                             ensure_ascii=False).encode("utf-8")
            _upload(sb, BUCKET_PUBLIC, "config.json", cfg,
                    "application/json; charset=utf-8", public=True)
            page = f"{sb.url}/storage/v1/object/public/{BUCKET_PUBLIC}/index.html"
            _verify_served(sb, BUCKET_PUBLIC, "index.html", page, cache_bust=data["generated_at"].replace(":",""))
            print(f"\n✅ 配信しました（公開モード）。ブラウザで下のURLを開いてください。\n  {page}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
