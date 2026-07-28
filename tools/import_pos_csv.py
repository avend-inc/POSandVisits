"""
過去データの一括取り込み — Supabase Storage(非公開バケット)に置いたPOSのCSVを
アダプタで共通スキーマに変換し、sales テーブルへ入れる（重複は自動で無視）。

使い方（GitHub Actions の「POS CSV 取り込み」から実行）:
  python -m tools.import_pos_csv --bucket imports --path si_history.csv \
      --adapter ezregi --pos-name Si --ownership FC --export

  --bucket/--path : 非公開バケット名 と ファイルパス
  --adapter       : ezregi(=Si明細) / cashier / airregi
  --pos-name      : sales.pos_name に入れる識別名（例: Si, AirREGI）
  --ownership     : 取り込みで新規作成/対象になった店舗の区分を一括設定（直営/FC）。省略で変更なし
  --export        : 取り込み後にダッシュボードの集計・配信も行う

※ Si(【Si】DB)のCSVは、シート上部に集計行が入っていることがあるため、
   「店舗コード…」の見出し行を自動で探して、そこから読み込む。必要な列だけ使う。
"""
from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import adapters  # noqa: E402
from etl.settings import EtlError, load_dotenv  # noqa: E402
from etl.supabase_client import Supabase  # noqa: E402

# アダプタが参照するSi列（この名前で拾う。余分な列は無視）
SI_NEEDED = ["店舗コード", "店舗名", "レジNo", "レシートNo", "購入日時",
             "商品分類名", "売上数", "本体価格", "税抜売価", "税込売価"]


def _download(sb: Supabase, bucket: str, path: str) -> bytes:
    url = f"{sb.url}/storage/v1/object/{bucket}/{path}"
    r = requests.get(url, headers={"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}, timeout=180)
    if r.status_code != 200:
        raise EtlError(f"CSVを取得できませんでした（HTTP {r.status_code}）: {r.text[:300]}\n"
                       f"  → 非公開バケット '{bucket}' に '{path}' がある/service_roleで読めるか確認してください。")
    return r.content


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_si_table(text: str) -> pd.DataFrame:
    """集計行を飛ばして「店舗コード…」見出しから読み、必要な列だけを返す。"""
    lines = text.splitlines()
    hdr = None
    for i, ln in enumerate(lines):
        if "店舗コード" in ln and "レシートNo" in ln:
            hdr = i
            break
    if hdr is None:
        raise EtlError("CSVに見出し行（店舗コード…レシートNo…）が見つかりませんでした。"
                       "【Si】DBのCSVか確認してください。")
    df = pd.read_csv(StringIO("\n".join(lines[hdr:])), dtype=str, engine="python", on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in SI_NEEDED if c not in df.columns]
    if missing:
        raise EtlError(f"必要な列がありません: {missing}\n  実際の列: {list(df.columns)[:25]}")
    df = df[[c for c in SI_NEEDED if c in df.columns]].copy()
    df = df[df["店舗名"].astype(str).str.strip() != ""]
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description="POSのCSVを一括取り込み")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--path", required=True)
    ap.add_argument("--adapter", default="ezregi", choices=["ezregi", "cashier", "airregi"])
    ap.add_argument("--pos-name", default="Si")
    ap.add_argument("--ownership", default="", choices=["", "直営", "FC"])
    ap.add_argument("--export", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    sb = Supabase()

    print(f"CSVを取得: {args.bucket}/{args.path}")
    text = _decode(_download(sb, args.bucket, args.path))

    if args.adapter == "ezregi":
        df_in = _read_si_table(text)
        common = adapters.adapt_ezregi(df_in, args.pos_name)
    elif args.adapter == "cashier":
        df_in = pd.read_csv(StringIO(text), dtype=str)
        common = adapters.adapt_cashier(df_in, args.pos_name)
    else:  # airregi
        df_in = pd.read_csv(StringIO(text), dtype=str)
        common = adapters.adapt_airregi(df_in, args.pos_name, args.pos_name)

    common = common[common["date"].notna()].copy()
    if len(common) == 0:
        print("取り込む明細が0件でした。"); return 0
    common["line_no"] = common.groupby("tx_id").cumcount()

    stores = sorted(common["store"].unique().tolist())
    print(f"明細 {len(common)}件 / 店舗 {len(stores)}店: {', '.join(stores[:20])}"
          + (" …" if len(stores) > 20 else ""))

    cache = sb.store_map()
    touched: set[int] = set()

    def store_id_of(name: str) -> int:
        sid = sb.get_or_create_store(name, cache)
        touched.add(sid)
        return sid

    from etl import rows as rows_mod
    payload = rows_mod.cashier_rows(common, store_id_of)
    inserted, duplicate = sb.insert_ignore_duplicates(
        "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no")
    print(f"sales へ保存: 新規 {inserted}行 / 既存で無視 {duplicate}行")

    if args.ownership and touched:
        ids = ",".join(str(i) for i in sorted(touched))
        url = f"{sb.url}/rest/v1/stores?id=in.({ids})"
        r = requests.patch(url, headers={
            "apikey": sb.key, "Authorization": f"Bearer {sb.key}",
            "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"ownership": args.ownership}, timeout=120)
        print(f"店舗区分を {args.ownership} に設定: HTTP {r.status_code}（対象 {len(touched)}店）")

    if args.export:
        print("ダッシュボードを更新します…")
        from tools import export_dashboard
        export_dashboard.main()  # 集計・配信

    print("✅ 取り込み完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
