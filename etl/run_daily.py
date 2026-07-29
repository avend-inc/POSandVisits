"""
日次ETL 本体 — cashier の売上と、デジテールの来店数を Supabase に入れる

【使い方】
  python -m etl.run_daily                     前日ぶんを取り込む（通常はこれ）
  python -m etl.run_daily --date 2026-07-20   特定の日を取り込む
  python -m etl.run_daily --only digitel      片方だけ動かす
  python -m etl.run_daily --force             取り込み済みの日をもう一度やる
  python -m etl.run_daily --headed            ブラウザの画面を出して動かす（調査用）

【安全のしくみ】
  1. 同じ営業日を二度取り込まない
     ingest_log に「その日は成功済み」の記録があれば、
     取りに行かずに NG として記録して終わる（--force で解除）。
  2. 万一二重に流し込んでも行は増えない
     sales / visits には一意キーがあり、
     「既にある行は無視して追加」という入れ方をしている。
  3. 何が起きたかを必ず残す
     成功でも失敗でも ingest_log に1行記録する。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime

from .settings import (
    JST,
    RAW_DIR,
    EtlError,
    load_dotenv,
    new_run_id,
    validate_date,
    yesterday_jst,
)
from .supabase_client import Supabase


# ============================================================
# 補助
# ============================================================
def now_iso() -> str:
    return datetime.now(JST).isoformat()


def save_raw(text: str, source: str, label: str, business_date: str) -> None:
    """
    加工前のCSVを残す。

    取り込みが壊れて数日気づかなくても、これがあれば後から復元できる。
    （raw/ は .gitignore 済み。GitHub Actions では成果物として保存する）
    """
    folder = RAW_DIR / source
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{business_date}_{label}.csv").write_text(text, encoding="utf-8-sig")


class StepResult:
    def __init__(self, name: str, status: str, message: str = "",
                 fetched: int = 0, inserted: int = 0, duplicate: int = 0):
        self.name = name
        self.status = status
        self.message = message
        self.fetched = fetched
        self.inserted = inserted
        self.duplicate = duplicate


# ============================================================
# cashier（売上）
# ============================================================
def run_cashier(sb: Supabase, business_date: str, run_id: str,
                force: bool, headless: bool, store_cache: dict) -> StepResult:
    from . import cashier_fetch, rows as rows_mod

    print("\n" + "=" * 60)
    print(f"【1】cashier 売上明細  対象営業日: {business_date}")
    print("=" * 60)

    started = now_iso()

    # --- 二重取り込みのチェック ---
    if not force and sb.already_succeeded("cashier", business_date, None):
        message = (f"{business_date} の cashier 売上は既に取り込み済みです。"
                   "二重取り込みは行いません（もう一度入れ直したい場合は --force）。")
        print(f"  ⛔ NG: {message}")
        sb.log(run_id=run_id, source="cashier", business_date=business_date,
               store_id=None, status="rejected_duplicate", message=message,
               started_at=started)
        return StepResult("cashier", "rejected_duplicate", message)

    try:
        csv_text = cashier_fetch.fetch(business_date, headless=headless)
        save_raw(csv_text, "cashier", "TradeDetail", business_date)

        df = rows_mod.parse_cashier_csv(csv_text, business_date)
        print(f"  CSVから読めた明細: {len(df)}行")

        if len(df) == 0:
            message = (f"{business_date} の売上明細は0行でした"
                       "（定休日・休業日ならこれで正常です）。")
            print(f"  ℹ️ {message}")
            sb.log(run_id=run_id, source="cashier", business_date=business_date,
                   store_id=None, status="no_data", message=message,
                   started_at=started)
            return StepResult("cashier", "no_data", message)

        def store_id_of(name: str) -> int:
            return sb.get_or_create_store(name, store_cache)

        payload = rows_mod.cashier_rows(df, store_id_of)
        inserted, duplicate = sb.insert_ignore_duplicates(
            "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no"
        )
        print(f"  Supabaseへ保存: 新規 {inserted}行 / 既存で無視 {duplicate}行")

        stores = sorted(df["store"].unique().tolist())
        sb.log(run_id=run_id, source="cashier", business_date=business_date,
               store_id=None, status="success",
               rows_fetched=len(payload), rows_inserted=inserted,
               rows_duplicate=duplicate,
               message=f"店舗: {', '.join(stores)}", started_at=started)
        return StepResult("cashier", "success", f"店舗: {', '.join(stores)}",
                          len(payload), inserted, duplicate)

    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        print(f"  ❌ 失敗: {detail}")
        if not isinstance(e, EtlError):
            traceback.print_exc()
        sb.log(run_id=run_id, source="cashier", business_date=business_date,
               store_id=None, status="failed", message=detail[:2000],
               started_at=started)
        return StepResult("cashier", "failed", detail)


# ============================================================
# デジテール（来店数）
# ============================================================
def run_digitel(sb: Supabase, business_date: str, run_id: str,
                force: bool, headless: bool) -> list[StepResult]:
    from . import digitel_fetch, rows as rows_mod

    print("\n" + "=" * 60)
    print(f"【2】デジテール 来店数  対象営業日: {business_date}")
    print("=" * 60)

    started = now_iso()

    stores = sb.select("stores", {
        "select": "id,name,digitel_slug",
        "has_entry_data": "eq.true",
        "digitel_slug": "not.is.null",
        "order": "id",
    })
    if not stores:
        message = ("入店データを取る店舗が stores テーブルに1件もありません。\n"
                   "  → sql/002_seed_stores.sql を Supabase の SQL Editor で実行してください。")
        print(f"  ❌ {message}")
        sb.log(run_id=run_id, source="digitel", business_date=business_date,
               store_id=None, status="failed", message=message, started_at=started)
        return [StepResult("digitel", "failed", message)]

    # 既に取り込み済みの店舗をよける
    targets, results = {}, []
    id_by_name = {}
    for store in stores:
        if not force and sb.already_succeeded("digitel", business_date, store["id"]):
            message = (f"{business_date} の【{store['name']}】来店数は既に取り込み済みです。"
                       "二重取り込みは行いません（--force で解除）。")
            print(f"  ⛔ NG: {message}")
            sb.log(run_id=run_id, source="digitel", business_date=business_date,
                   store_id=store["id"], status="rejected_duplicate",
                   message=message, started_at=started)
            results.append(StepResult(f"digitel/{store['name']}",
                                      "rejected_duplicate", message))
            continue
        targets[store["name"]] = store["digitel_slug"]
        id_by_name[store["name"]] = store["id"]

    if not targets:
        return results

    # --- まとめて取得 ---
    try:
        csv_by_store = digitel_fetch.fetch(targets, business_date, business_date,
                                           headless=headless)
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        print(f"  ❌ 失敗: {detail}")
        for name, store_id in id_by_name.items():
            sb.log(run_id=run_id, source="digitel", business_date=business_date,
                   store_id=store_id, status="failed", message=detail[:2000],
                   started_at=started)
            results.append(StepResult(f"digitel/{name}", "failed", detail))
        return results

    # --- 店舗ごとに保存 ---
    for name, store_id in id_by_name.items():
        csv_text = csv_by_store.get(name)
        if csv_text is None:
            message = "CSVを取得できませんでした（上のログを確認してください）。"
            sb.log(run_id=run_id, source="digitel", business_date=business_date,
                   store_id=store_id, status="failed", message=message,
                   started_at=started)
            results.append(StepResult(f"digitel/{name}", "failed", message))
            continue

        try:
            save_raw(csv_text, "digitel", name, business_date)
            payload = rows_mod.visits_rows(csv_text, store_id, business_date)

            if not payload:
                message = (f"{business_date} の【{name}】来店数がCSVに含まれていません"
                           "（デジテール側の反映待ちの可能性があります）。")
                print(f"  ℹ️ {message}")
                sb.log(run_id=run_id, source="digitel", business_date=business_date,
                       store_id=store_id, status="no_data", message=message,
                       started_at=started)
                results.append(StepResult(f"digitel/{name}", "no_data", message))
                continue

            # 来店数は「その営業日の確定値」なので上書き（同じ日を取り直したら最新値へ）
            affected = sb.upsert(
                "visits", payload, on_conflict="business_date,store_id,source"
            )
            visitors = payload[0].get("visitors")
            print(f"  【{name}】来店 {visitors}人 → 反映 {affected}行（新規/更新）")

            sb.log(run_id=run_id, source="digitel", business_date=business_date,
                   store_id=store_id, status="success",
                   rows_fetched=len(payload), rows_inserted=affected,
                   rows_duplicate=0, message=f"来店客数 {visitors}",
                   started_at=started)
            results.append(StepResult(f"digitel/{name}", "success",
                                      f"来店 {visitors}人",
                                      len(payload), affected, 0))

        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            print(f"  【{name}】❌ 失敗: {detail}")
            sb.log(run_id=run_id, source="digitel", business_date=business_date,
                   store_id=store_id, status="failed", message=detail[:2000],
                   started_at=started)
            results.append(StepResult(f"digitel/{name}", "failed", detail))

    return results


# ============================================================
# バンドル（SALE）の名称マスタ — cashierの「バンドル」画面から自動取得
# ============================================================
def run_bundle_master(sb: Supabase, business_date: str, run_id: str,
                      force: bool, headless: bool) -> StepResult:
    from . import cashier_fetch
    import io
    import pandas as pd

    print("\n" + "=" * 60)
    print("【1b】バンドル名（コード→名称）を更新")
    print("=" * 60)
    started = now_iso()

    if not force and sb.already_succeeded("bundle", business_date, None):
        msg = f"{business_date} のバンドル名は取得済み（--force で再取得）。"
        print(f"  ⛔ {msg}")
        return StepResult("bundle_master", "rejected_duplicate", msg)

    try:
        text = cashier_fetch.fetch_bundle(headless=headless)
        df = pd.read_csv(io.StringIO(text), dtype=str).fillna("")
        df.columns = [str(c).strip() for c in df.columns]

        def col(*names):
            return next((n for n in names if n in df.columns), None)
        c_code = col("バンドルコード", "コード", "bundle_code")
        c_name = col("バンドル名", "名称", "bundle_name")
        c_cat = col("商品カテゴリ名", "カテゴリ", "category")
        if not c_code or not c_name:
            raise EtlError(f"バンドルCSVに必要な列がありません。実際の列: {list(df.columns)[:15]}")

        rows, seen = [], set()
        for _, r in df.iterrows():
            code = str(r[c_code]).strip()
            if not code or code in seen:
                continue
            seen.add(code)
            rows.append({"code": code, "name": str(r[c_name]).strip(),
                         "category": (str(r[c_cat]).strip() if c_cat else None)})
        n = sb.upsert("bundle_master", rows, on_conflict="code") if rows else 0
        print(f"  bundle_master へ upsert: {len(rows)}件")
        sb.log(run_id=run_id, source="bundle", business_date=business_date,
               store_id=None, status="success", rows_fetched=len(rows),
               rows_inserted=n, message=f"{len(rows)}件", started_at=started)
        return StepResult("bundle_master", "success", f"{len(rows)}件", n, 0)
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        print(f"  ❌ バンドル名の取得に失敗: {detail}")
        if not isinstance(e, EtlError):
            traceback.print_exc()
        sb.log(run_id=run_id, source="bundle", business_date=business_date,
               store_id=None, status="failed", message=detail[:2000], started_at=started)
        return StepResult("bundle_master", "failed", detail)


# ============================================================
# メイン
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="NOTIME 日次ETL（cashier売上 / デジテール来店数 → Supabase）"
    )
    parser.add_argument("--date", help="対象の営業日 YYYY-MM-DD（省略時は前日）")
    parser.add_argument("--only", choices=["cashier", "digitel", "pos", "bundle"],
                        help="1種類だけ動かす（cashier / digitel / pos=Air/EZ接続 / bundle=バンドル名）")
    parser.add_argument("--force", action="store_true",
                        help="取り込み済みの日でも、もう一度取り込む")
    parser.add_argument("--headed", action="store_true",
                        help="ブラウザの画面を出して動かす（調査用）")
    args = parser.parse_args()

    load_dotenv()

    business_date = validate_date(args.date) if args.date else yesterday_jst()
    run_id = new_run_id()
    headless = not args.headed

    print("=" * 60)
    print("NOTIME 日次ETL")
    print(f"  実行ID     : {run_id}")
    print(f"  対象営業日 : {business_date}（日本時間）")
    print(f"  対象        : {args.only or 'cashier + デジテール'}")
    if args.force:
        print("  ⚠️ --force 指定: 取り込み済みでも再取り込みします")
    print("=" * 60)

    try:
        sb = Supabase()
        store_cache = sb.store_map()
    except EtlError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return 1

    results: list[StepResult] = []
    if args.only in (None, "cashier"):
        results.append(run_cashier(sb, business_date, run_id, args.force,
                                   headless, store_cache))
    if args.only == "bundle":
        # バンドル名（SALEの名称マスタ）の自動取得。cashierの「バンドル」画面はCSV出力の
        # 目印が未確定のため、既定の日次では回さず、明示指定(--only bundle)のときだけ試す。
        # 名称は通常 sql seed / 手動「バンドル名 取り込み」で入れる（名称は滅多に変わらない）。
        results.append(run_bundle_master(sb, business_date, run_id, args.force, headless))
    if args.only in (None, "digitel"):
        results.extend(run_digitel(sb, business_date, run_id, args.force, headless))
    if args.only in (None, "pos"):
        # Air/EZ等の「レジ接続」（store_pos）を巡回して取り込む。未登録なら何もしない。
        from .pos_live import run_live_pos
        results.extend(run_live_pos(sb, business_date, run_id, args.force, headless))

    # ---- まとめ ----
    icons = {"success": "✅", "no_data": "ℹ️",
             "rejected_duplicate": "⛔", "failed": "❌"}
    print("\n" + "=" * 60)
    print("結果")
    print("=" * 60)
    for r in results:
        line = f"  {icons.get(r.status, '　')} {r.name:<18} {r.status}"
        if r.status == "success":
            line += f"  新規 {r.inserted}行 / 無視 {r.duplicate}行"
        print(line)
        if r.message and r.status in ("failed", "rejected_duplicate"):
            print(f"      {r.message.splitlines()[0]}")

    # バンドル名(SALEの名称マスタ)は best-effort。失敗しても全体は止めない
    #  （売上・来店の取り込みが成功していれば正常終了扱いにする）。
    BEST_EFFORT = {"bundle_master"}
    failed = [r for r in results if r.status == "failed" and r.name not in BEST_EFFORT]
    warn = [r for r in results if r.status == "failed" and r.name in BEST_EFFORT]
    rejected = [r for r in results if r.status == "rejected_duplicate"]

    if warn:
        print("\n⚠️ バンドル名の取得はスキップ/失敗しました（best-effort。他の取り込みには影響しません）。")
    if failed:
        print(f"\n❌ {len(failed)}件が失敗しました。ingest_log に記録済みです。")
        return 1
    if rejected and len(rejected) == len(results):
        # 既に取り込み済みの日をもう一度動かしただけ（予備スケジュール・再実行・テスト等）。
        # これは異常ではなく“何もすることが無かった”だけなので、正常終了(0)にする。
        # （こうしないと予備時刻の実行が毎回「失敗(赤)」に見えて紛らわしい）
        print("\nℹ️ すべて既に取り込み済みでした（重複取り込みはしていません）。正常終了します。")
        return 0
    print("\n✅ 取り込みが完了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
