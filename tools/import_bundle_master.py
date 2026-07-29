"""
バンドル（SALE）の「コード→名称」対応表を取り込む

cashierの「バンドル」CSV（バンドルコード / バンドル名 / 商品カテゴリ名 …）を
Supabase Storage の非公開バケットから読み、bundle_master に upsert する。

使い方（GitHub Actions の「バンドル名 取り込み」から実行）:
  python -m tools.import_bundle_master --bucket imports --path ClientBundle.csv

  取得手順（このCSVの作り方）: Cashier → バンドル → 期間設定 → CSVダウンロード
  ※ 認証情報は既存の SUPABASE_URL / SUPABASE_SERVICE_KEY を流用します。
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

from etl.settings import EtlError, load_dotenv  # noqa: E402
from etl.supabase_client import Supabase  # noqa: E402


def _download(sb: Supabase, bucket: str, path: str) -> bytes:
    url = f"{sb.url}/storage/v1/object/{bucket}/{path}"
    r = requests.get(url, headers={"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}, timeout=180)
    if r.status_code != 200:
        raise EtlError(f"CSVを取得できませんでした（HTTP {r.status_code}）: {r.text[:200]}\n"
                       f"  → 非公開バケット '{bucket}' に '{path}' があるか確認してください。")
    return r.content


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser(description="バンドル名(コード→名称)の取り込み")
    ap.add_argument("--bucket", default="imports")
    ap.add_argument("--path", required=True)
    args = ap.parse_args()

    load_dotenv()
    sb = Supabase()

    print(f"CSVを取得: {args.bucket}/{args.path}")
    text = _decode(_download(sb, args.bucket, args.path))
    df = pd.read_csv(StringIO(text), dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]

    def col(*names):
        for n in names:
            if n in df.columns:
                return n
        raise EtlError(f"必要な列がありません（{names}）。実際の列: {list(df.columns)[:20]}")

    c_code = col("バンドルコード", "コード", "bundle_code")
    c_name = col("バンドル名", "名称", "bundle_name")
    c_cat = next((n for n in ("商品カテゴリ名", "カテゴリ", "category") if n in df.columns), None)

    rows, seen = [], set()
    for _, r in df.iterrows():
        code = str(r[c_code]).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        rows.append({"code": code, "name": str(r[c_name]).strip(),
                     "category": (str(r[c_cat]).strip() if c_cat else None)})
    if not rows:
        print("取り込む行がありませんでした。"); return 0

    n = sb.upsert("bundle_master", rows, on_conflict="code")
    print(f"bundle_master へ upsert: {len(rows)}件（反映 {n}）")
    print("  例: " + ", ".join(f"{x['code']}={x['name']}" for x in rows[:5]))
    print("✅ 取り込み完了。次のダッシュボード更新で名称が反映されます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
