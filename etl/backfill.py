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
    """
    デジテールの過去来店数を、2アカウント（NOTIME / SELFURUGI）で
    “見える全店舗を自動発見”して期間まるごと取り込む（日次と同じやり方）。
    """
    import os
    from . import digitel_fetch, rows as rows_mod
    from .settings import store_key

    print("\n" + "=" * 60)
    print(f"【2】デジテール 来店数  期間: {start} 〜 {end}")
    print("=" * 60)

    all_stores = sb.select("stores", {"select": "*"})   # digitel_sales 未追加でも落ちない
    slug_to_id = {s["digitel_slug"]: s["id"] for s in all_stores if s.get("digitel_slug")}
    name_cache = {s["name"]: s["id"] for s in all_stores}
    sales_slugs = {s["digitel_slug"] for s in all_stores
                   if s.get("digitel_sales") and s.get("digitel_slug")}

    accounts = [("NOTIME", None, None)]
    sf_id = os.environ.get("DIGITAIL_SF_ID", "").strip()
    sf_pw = os.environ.get("DIGITAIL_SF_PW", "").strip()
    if sf_id and sf_pw:
        accounts.append(("SELFURUGI", sf_id, sf_pw))
    else:
        print("  ℹ️ SELFURUGIアカウント未登録のため、NOTIMEぶんだけ取得します。")

    for label, user, pw in accounts:
        print(f"\n--- デジテール {label} アカウント ---")
        try:
            found = digitel_fetch.fetch_all(
                start, end, headless=headless, user=user, password=pw,
                sales_slugs=sales_slugs)
        except Exception as e:
            print(f"  ❌ {label}アカウント失敗: {type(e).__name__}: {e}")
            continue

        for name, info in found.items():
            slug, csv_text = info["slug"], info["csv"]
            store_id = slug_to_id.get(slug)
            if store_id is None:
                nkey = store_key(name)
                is_new = not any(store_key(nm) == nkey for nm in name_cache)
                store_id = sb.get_or_create_store(name, name_cache)
                patch = {"digitel_slug": slug, "has_entry_data": True}
                if is_new:
                    patch["ownership"] = "FC"
                try:
                    sb.update_store(store_id, patch)
                except Exception as e:
                    print(f"  （店舗への digitel_slug 記録は後回し：{e}）")
                slug_to_id[slug] = store_id

            payload = rows_mod.visits_rows(csv_text, store_id, business_date=None)
            if not payload:
                print(f"  【{name}】対象期間の来店データは0日でした")
                continue
            affected = sb.upsert(
                "visits", payload, on_conflict="business_date,store_id,source")
            days = sorted(r["business_date"] for r in payload)
            print(f"  【{name}】{days[0]} 〜 {days[-1]}（{len(payload)}日）"
                  f" → 反映 {affected}日（新規/更新）")

            # 売上もデジテールから取る店（伊予松前など）は、期間の売上もsalesへ。
            # 商品明細(sales_detail)があればカテゴリ付きで、無ければ日次合計で。
            detail = info.get("sales_detail")
            scsv = info.get("sales_csv")
            if (detail and detail.get("details")) or scsv:
                try:
                    if detail and detail.get("details"):
                        spay = rows_mod.digitel_detail_rows(
                            detail.get("sales", ""), detail["details"],
                            store_id, business_date=None)
                        kind = "明細"
                    else:
                        spay = rows_mod.digitel_sales_rows(scsv, store_id, business_date=None)
                        kind = "合計"
                    sb.delete("sales", {
                        "store_id": f"eq.{store_id}",
                        "pos_name": f"eq.{rows_mod.DIGITEL_SALES_POS}",
                        "and": f"(business_date.gte.{start},business_date.lte.{end})"})
                    if spay:
                        ins, _ = sb.insert_ignore_duplicates(
                            "sales", spay, on_conflict="store_id,pos_name,tx_id,line_no")
                        sdays = sorted({r["business_date"] for r in spay})
                        txn = len({(r["business_date"], r["tx_id"]) for r in spay})
                        ssum = sum(r["sales_in_tax"] for r in spay if r["line_no"] == 0)
                        print(f"  【{name}】デジテール売上({kind}) {sdays[0]}〜{sdays[-1]}"
                              f"（{txn}取引 / {int(ssum):,}円）→ 追加 {ins}行")
                except Exception as e:
                    print(f"  【{name}】デジテール売上 ❌ {type(e).__name__}: {e}")


def run_smaregi_backfill(sb: Supabase, start: str, end: str, headless: bool,
                         store_cache: dict) -> None:
    """スマレジ（隠岐）の過去分。SMAREGI_ID/PW が無ければ何もしない。"""
    import os
    user = os.environ.get("SMAREGI_ID") or ""
    pw = os.environ.get("SMAREGI_PW") or ""
    if not user or not pw:
        print("  ℹ️ SMAREGI_ID/PW 未設定のため スマレジ backfill はスキップ。")
        return

    from datetime import date as _date, timedelta
    from . import smaregi_fetch, rows as rows_mod
    from .run_daily import SMAREGI_STORE_NAME

    print("\n--- スマレジ（隠岐）売上 ---")
    store_id = sb.get_or_create_store(SMAREGI_STORE_NAME, store_cache)
    try:
        sb.update_store(store_id, {"ownership": "FC"})
    except Exception:
        pass

    def _d(s):
        y, m, d = map(int, s.split("-")); return _date(y, m, d)

    def _month_end(d):
        nm = _date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
        return nm - timedelta(days=1)

    # スマレジは1日ずつAPIを叩くため、長期間は月ごとに「取得→即保存」する。
    # こうすると途中で切れても月単位で結果が残り、再実行にも強い。
    total_ins = 0
    failed_months: list[str] = []
    cur = _d(start); last = _d(end)
    while cur <= last:
        m_start = cur
        m_end = min(_month_end(cur), last)
        ym = m_start.isoformat()[:7]
        try:
            got = smaregi_fetch.fetch_range(m_start.isoformat(), m_end.isoformat(),
                                            user, pw, headless=headless)
            payload: list[dict] = []
            for iso in sorted(got.keys()):
                day = got[iso]
                payload.extend(rows_mod.smaregi_rows(day.get("info") or {},
                                                     day.get("categories") or [],
                                                     store_id, iso))
            sb.delete("sales", {
                "store_id": f"eq.{store_id}",
                "pos_name": f"eq.{rows_mod.SMAREGI_POS}",
                "and": f"(business_date.gte.{m_start.isoformat()},"
                       f"business_date.lte.{m_end.isoformat()})"})
            if payload:
                ins, _ = sb.insert_ignore_duplicates(
                    "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no")
                total_ins += ins
                txn = len({(r["business_date"], r["tx_id"]) for r in payload})
                ssum = sum(r["sales_in_tax"] for r in payload if r["line_no"] == 0)
                print(f"  【{SMAREGI_STORE_NAME}】{ym}: "
                      f"{txn}取引 / {int(ssum):,}円 → 追加 {ins}行")
            else:
                print(f"  【{SMAREGI_STORE_NAME}】{ym}: 売上0")
        except Exception as e:
            # 1か月がダメでも他の月は続ける（後で失敗月だけ流し直せる）。
            failed_months.append(ym)
            print(f"  【{SMAREGI_STORE_NAME}】{ym}: ❌ 取得失敗（スキップ）: "
                  f"{type(e).__name__}: {e}")
        cur = m_end + timedelta(days=1)
    if failed_months:
        print(f"  ⚠️ スマレジ backfill: 追加 {total_ins}行。"
              f"失敗月（要リトライ）: {', '.join(failed_months)}")
    else:
        print(f"  ✅ スマレジ backfill 完了（合計 追加 {total_ins}行）")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NOTIME 過去分の一括取り込み（backfill）"
    )
    parser.add_argument("--from", dest="date_from", default=DEFAULT_FROM,
                        help=f"開始日 YYYY-MM-DD（既定 {DEFAULT_FROM}）")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="終了日 YYYY-MM-DD（既定は今日）")
    parser.add_argument("--only", choices=["cashier", "digitel", "smaregi"],
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
    if args.only in (None, "cashier"):
        try:
            run_cashier_backfill(sb, start, end, headless, store_cache)
        except Exception as e:
            failed = True
            print(f"  ❌ cashier backfill 失敗: {type(e).__name__}: {e}")

    if args.only in (None, "digitel"):
        try:
            run_digitel_backfill(sb, start, end, headless)
        except Exception as e:
            failed = True
            print(f"  ❌ デジテール backfill 失敗: {type(e).__name__}: {e}")

    if args.only in (None, "smaregi"):
        try:
            run_smaregi_backfill(sb, start, end, headless, store_cache)
        except Exception as e:
            failed = True
            print(f"  ❌ スマレジ backfill 失敗: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    if failed:
        print("⚠️ 一部が失敗しました。上のログを確認してください。")
        return 1
    print("✅ 過去分の一括取り込みが完了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
