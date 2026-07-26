"""
過去分の一括取り込み（backfill）

【何のため？】
  日次ETLは「前日ぶん」を毎日ためていく。
  だが、稼働前の過去データ（各店オープン前後の売上・来店）は入っていない。
  これを1回だけまとめて取り込むための道具。

【使い方】
  python -m etl.backfill                       # 既定範囲（各店オープン1週間前〜今日）
  python -m etl.backfill --from 2026-03-13     # 開始日を指定
  python -m etl.backfill --from 2026-03-13 --to 2026-07-26
  python -m etl.backfill --only cashier        # 片方だけ
  python -m etl.backfill --headed              # 画面を見ながら

【設計メモ】
  ・cashier: 1回のログインで「開始〜終了」の明細CSVを一括取得 → 全期間を保存。
    店舗名は adapters が正規化（いわきの2ラベルも「いわき」に合算される）。
  ・デジテール: 店舗ごとに期間CSV（1日1行）を取得 → 全日を保存。
  ・二重登録は sales/visits の一意キーで自動的に無視される（何度流しても安全）。
  ・ingest_log には書かない（日次の実行記録を汚さないため）。件数は画面に出す。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .settings import (
    JST,
    EtlError,
    load_dotenv,
    today_jst,
    validate_date,
)
from .supabase_client import Supabase

# 既定の開始日 = いちばん早い店（山形 2026-03-20）のオープン1週間前
DEFAULT_FROM = "2026-03-13"


def run_cashier_backfill(sb: Supabase, start: str, end: str,
                         headless: bool, store_cache: dict) -> None:
    from . import cashier_fetch, rows as rows_mod

    print("\n" + "=" * 60)
    print(f"【1】cashier 売上明細  期間: {start} 〜 {end}")
    print("=" * 60)

    csv_text = cashier_fetch.fetch_range(start, end, headless=headless)
    df = rows_mod.parse_cashier_csv(csv_text, business_date=None)  # 全期間
    print(f"  CSVから読めた明細: {len(df)}行")
    if len(df) == 0:
        print("  ℹ️ 対象期間の明細は0行でした。")
        return

    def store_id_of(name: str) -> int:
        return sb.get_or_create_store(name, store_cache)

    payload = rows_mod.cashier_rows(df, store_id_of)
    inserted, duplicate = sb.insert_ignore_duplicates(
        "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no"
    )
    stores = sorted(df["store"].unique().tolist())
    dates = sorted(df["date"].unique().tolist())
    print(f"  店舗: {', '.join(stores)}")
    print(f"  日付: {dates[0]} 〜 {dates[-1]}（{len(dates)}日ぶん）")
    print(f"  Supabaseへ保存: 新規 {inserted}行 / 既存で無視 {duplicate}行")


def run_digitel_backfill(sb: Supabase, start: str, end: str,
                         headless: bool) -> None:
    from . import digitel_fetch, rows as rows_mod

    print("\n" + "=" * 60)
    print(f"【2】デジテール 来店数  期間: {start} 〜 {end}")
    print("=" * 60)

    stores = sb.select("stores", {
        "select": "id,name,digitel_slug",
        "has_entry_data": "eq.true",
        "digitel_slug": "not.is.null",
        "order": "id",
    })
    if not stores:
        print("  ❌ 対象店舗が stores にありません（sql/setup_all.sql を実行済みか確認）。")
        return

    slugs = {s["name"]: s["digitel_slug"] for s in stores}
    id_by_name = {s["name"]: s["id"] for s in stores}

    csv_by_store = digitel_fetch.fetch(slugs, start, end, headless=headless)

    for name, store_id in id_by_name.items():
        csv_text = csv_by_store.get(name)
        if csv_text is None:
            print(f"  【{name}】❌ CSVを取得できませんでした")
            continue
        payload = rows_mod.visits_rows(csv_text, store_id, business_date=None)
        if not payload:
            print(f"  【{name}】対象期間の来店データは0日でした")
            continue
        inserted, duplicate = sb.insert_ignore_duplicates(
            "visits", payload, on_conflict="business_date,store_id,source"
        )
        days = sorted(r["business_date"] for r in payload)
        print(f"  【{name}】{days[0]} 〜 {days[-1]}（{len(payload)}日）"
              f" → 新規 {inserted}日 / 無視 {duplicate}日")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NOTIME 過去分の一括取り込み（backfill）"
    )
    parser.add_argument("--from", dest="date_from", default=DEFAULT_FROM,
                        help=f"開始日 YYYY-MM-DD（既定 {DEFAULT_FROM}）")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="終了日 YYYY-MM-DD（既定は今日）")
    parser.add_argument("--only", choices=["cashier", "digitel"],
                        help="片方だけ動かす")
    parser.add_argument("--headed", action="store_true",
                        help="ブラウザの画面を出して動かす")
    args = parser.parse_args()

    load_dotenv()

    start = validate_date(args.date_from)
    end = validate_date(args.date_to) if args.date_to else today_jst()
    if start > end:
        print(f"❌ 開始日({start})が終了日({end})より後です。", file=sys.stderr)
        return 1
    headless = not args.headed

    print("=" * 60)
    print("NOTIME 過去分の一括取り込み（backfill）")
    print(f"  期間 : {start} 〜 {end}（日本時間）")
    print(f"  対象 : {args.only or 'cashier + デジテール'}")
    print("  ※ 二重登録は一意キーで自動的に無視されます（何度流しても安全）")
    print("=" * 60)

    try:
        sb = Supabase()
        store_cache = sb.store_map()
    except EtlError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return 1

    failed = False
    if args.only != "digitel":
        try:
            run_cashier_backfill(sb, start, end, headless, store_cache)
        except Exception as e:
            failed = True
            print(f"  ❌ cashier backfill 失敗: {type(e).__name__}: {e}")

    if args.only != "cashier":
        try:
            run_digitel_backfill(sb, start, end, headless)
        except Exception as e:
            failed = True
            print(f"  ❌ デジテール backfill 失敗: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    if failed:
        print("⚠️ 一部が失敗しました。上のログを確認してください。")
        return 1
    print("✅ 過去分の一括取り込みが完了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
