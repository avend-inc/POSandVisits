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
import os
import sys
import traceback
from datetime import datetime, timedelta

from .settings import (
    JST,
    RAW_DIR,
    EtlError,
    load_dotenv,
    new_run_id,
    store_key,
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
    置き場所は settings.RAW_DIR。自分のPCならリポジトリ直下の raw/（.gitignore済み）、
    GitHub Actions ならランナーの一時領域（作業フォルダの外）。
    Actions で作業フォルダの中に置くと、失敗時のアーティファクトに全店の売上明細が
    そのまま入り、リポジトリのread権限がある人なら誰でも落とせてしまうため。
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

    # 店舗マスタを1回だけ読み込む。スラッグ→店id、名前→店id を作っておく。
    # "*" にしておくと digitel_sales 列が未追加(sql/016前)でも落ちない（.getで既定None）
    all_stores = sb.select("stores", {"select": "*"})
    slug_to_id: dict[str, int] = {
        s["digitel_slug"]: s["id"] for s in all_stores if s.get("digitel_slug")
    }
    name_cache: dict[str, int] = {s["name"]: s["id"] for s in all_stores}
    # 売上もデジテールから取る店（例: 伊予松前）のスラッグ集合
    sales_slugs: set[str] = {
        s["digitel_slug"] for s in all_stores
        if s.get("digitel_sales") and s.get("digitel_slug")
    }

    # 使うアカウント。NOTIMEは既定の DIGITAIL_ID/PW。
    # SELFURUGIは DIGITAIL_SF_ID/PW が登録されていれば追加で取る。
    accounts: list[tuple[str, str | None, str | None]] = [("NOTIME", None, None)]
    sf_id = os.environ.get("DIGITAIL_SF_ID", "").strip()
    sf_pw = os.environ.get("DIGITAIL_SF_PW", "").strip()
    if sf_id and sf_pw:
        accounts.append(("SELFURUGI", sf_id, sf_pw))
    else:
        print("  ℹ️ SELFURUGIアカウント（DIGITAIL_SF_ID / DIGITAIL_SF_PW）が未登録のため、"
              "NOTIMEアカウントぶんだけ取得します。")

    results: list[StepResult] = []

    for label, user, pw in accounts:
        print(f"\n--- デジテール {label} アカウント ---")

        # このアカウントで“見える全店舗”を自動で見つけて、まとめて取得
        try:
            found = digitel_fetch.fetch_all(
                business_date, business_date,
                headless=headless, user=user, password=pw,
                sales_slugs=sales_slugs,
            )
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            print(f"  ❌ {label}アカウント失敗: {detail}")
            sb.log(run_id=run_id, source="digitel", business_date=business_date,
                   store_id=None, status="failed",
                   message=f"[{label}] {detail}"[:2000], started_at=started)
            results.append(StepResult(f"digitel/{label}", "failed", detail))
            continue

        for name, info in found.items():
            slug, csv_text = info["slug"], info["csv"]

            # 店idを決める（スラッグ優先 → 店名 → 自動追加）。
            # 新しく見つかった店には digitel_slug を書き込んで、次回からスラッグで安定して引けるようにする。
            store_id = slug_to_id.get(slug)
            if store_id is None:
                # 正規化キーで既存店を探す（「NOTIME天王台店」と「NOTIME天王台」を同じ扱いに）。
                # 本当に初めての店だけ ownership='FC' を付ける。
                nkey = store_key(name)
                is_new_store = not any(store_key(nm) == nkey for nm in name_cache)
                store_id = sb.get_or_create_store(name, name_cache)
                patch = {"digitel_slug": slug, "has_entry_data": True}
                # デジテールで“初めて”見つかる店はFC。
                #  （直営の4店＝山形/いわき/福井/下北沢 は既にマスタにあるので、
                #    ここを通るのは新しいFC店だけ。既存店の区分は勝手に変えない。
                #    stores.ownership の既定が「直営」なので、明示しないと直営に混ざってしまう）
                if is_new_store:
                    patch["ownership"] = "FC"
                try:
                    sb.update_store(store_id, patch)
                except Exception as e:
                    print(f"  （店舗への digitel_slug 記録は後回しにします：{e}）")
                slug_to_id[slug] = store_id

            # 既に取り込み済みならスキップ（--force で解除）
            if not force and sb.already_succeeded("digitel", business_date, store_id):
                message = (f"{business_date} の【{name}】来店数は既に取り込み済みです。"
                           "二重取り込みは行いません（--force で解除）。")
                print(f"  ⛔ NG: {message}")
                sb.log(run_id=run_id, source="digitel", business_date=business_date,
                       store_id=store_id, status="rejected_duplicate",
                       message=message, started_at=started)
                results.append(StepResult(f"digitel/{name}",
                                          "rejected_duplicate", message))
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

                # 売上もデジテールから取る店（伊予松前など）は、売上もsalesへ入れる。
                # 商品明細(sales_detail)があればカテゴリ付きで、無ければ日次合計で。
                detail = info.get("sales_detail")
                scsv = info.get("sales_csv")
                if (detail and detail.get("details")) or scsv:
                    try:
                        if detail and detail.get("details"):
                            spay = rows_mod.digitel_detail_rows(
                                detail.get("sales", ""), detail["details"],
                                store_id, business_date)
                            kind = "明細"
                        else:
                            spay = rows_mod.digitel_sales_rows(scsv, store_id, business_date)
                            kind = "合計"
                        # 同じ日のDIGITEL売上を消してから入れ直す（回数が変わっても整合）
                        sb.delete("sales", {
                            "store_id": f"eq.{store_id}",
                            "pos_name": f"eq.{rows_mod.DIGITEL_SALES_POS}",
                            "business_date": f"eq.{business_date}"})
                        if spay:
                            ins, _ = sb.insert_ignore_duplicates(
                                "sales", spay,
                                on_conflict="store_id,pos_name,tx_id,line_no")
                            txn = len({r["tx_id"] for r in spay})
                            sales_sum = sum(
                                r["sales_in_tax"] for r in spay if r["line_no"] == 0)
                            print(f"  【{name}】デジテール売上({kind}) {txn}取引 / "
                                  f"{int(sales_sum):,}円 → 追加 {ins}行")
                    except Exception as e:
                        print(f"  【{name}】デジテール売上 ❌ {type(e).__name__}: {e}")

            except Exception as e:
                detail = f"{type(e).__name__}: {e}"
                print(f"  【{name}】❌ 失敗: {detail}")
                sb.log(run_id=run_id, source="digitel", business_date=business_date,
                       store_id=store_id, status="failed", message=detail[:2000],
                       started_at=started)
                results.append(StepResult(f"digitel/{name}", "failed", detail))

    if not results:
        message = "デジテールで取得できた店舗がありませんでした。"
        print(f"  ❌ {message}")
        sb.log(run_id=run_id, source="digitel", business_date=business_date,
               store_id=None, status="failed", message=message, started_at=started)
        return [StepResult("digitel", "failed", message)]

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


# スマレジ店の表示名（SELFURUGIブランドFCとして扱う）。必要なら環境変数で上書き可。
SMAREGI_STORE_NAME = os.environ.get("SMAREGI_STORE_NAME", "SELFURUGI隠岐店")


def run_smaregi(sb: Supabase, business_date: str, run_id: str,
                force: bool, headless: bool, store_cache: dict) -> list[StepResult]:
    """スマレジ（隠岐）の売上を取り込む。SMAREGI_ID/PW が無ければ何もしない。"""
    user = os.environ.get("SMAREGI_ID") or ""
    pw = os.environ.get("SMAREGI_PW") or ""
    if not user or not pw:
        return []   # 未設定なら静かにスキップ

    from . import smaregi_fetch, rows as rows_mod
    started = datetime.now(JST).isoformat()
    print("\n" + "=" * 60)
    print(f"【3】スマレジ 売上  対象営業日: {business_date}")
    print("=" * 60)
    try:
        store_id = sb.get_or_create_store(SMAREGI_STORE_NAME, store_cache)
        try:
            sb.update_store(store_id, {"ownership": "FC"})
        except Exception:
            pass
        if not force and sb.already_succeeded("smaregi", business_date, store_id):
            print("  すでに取り込み済みのためスキップ（--force で解除）。")
            return [StepResult("smaregi", "rejected_duplicate", "取り込み済み")]

        got = smaregi_fetch.fetch_range(business_date, business_date, user, pw,
                                        headless=headless)
        day = got.get(business_date) or {}
        payload = rows_mod.smaregi_rows(day.get("info") or {},
                                        day.get("categories") or [],
                                        store_id, business_date)
        # 同じ日のSMAREGI売上を消してから入れ直す
        sb.delete("sales", {
            "store_id": f"eq.{store_id}",
            "pos_name": f"eq.{rows_mod.SMAREGI_POS}",
            "business_date": f"eq.{business_date}"})
        if not payload:
            sb.log(run_id=run_id, source="smaregi", business_date=business_date,
                   store_id=store_id, status="no_data", message="売上0",
                   started_at=started)
            print("  売上0（取り込むものなし）。")
            return [StepResult("smaregi", "no_data", "売上0")]
        ins, _ = sb.insert_ignore_duplicates(
            "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no")
        txn = len({r["tx_id"] for r in payload})
        ssum = sum(r["sales_in_tax"] for r in payload if r["line_no"] == 0)
        print(f"  【{SMAREGI_STORE_NAME}】スマレジ売上 {txn}取引 / {int(ssum):,}円 → 追加 {ins}行")
        sb.log(run_id=run_id, source="smaregi", business_date=business_date,
               store_id=store_id, status="success", rows_fetched=len(payload),
               rows_inserted=ins, message=f"{txn}取引 {int(ssum)}円", started_at=started)
        return [StepResult("smaregi", "success", f"{txn}取引 {int(ssum):,}円", len(payload), ins)]
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        print(f"  ❌ スマレジ取り込み失敗: {detail}")
        if not isinstance(e, EtlError):
            traceback.print_exc()
        sb.log(run_id=run_id, source="smaregi", business_date=business_date,
               store_id=None, status="failed", message=detail[:2000], started_at=started)
        return [StepResult("smaregi", "failed", detail)]


# Airレジ店（下北沢の2つ目のレジ）。表示名は下北沢に寄せる（別名で NOTIME下北沢店）。
AIRREGI_STORE_NAME = os.environ.get("AIRREGI_STORE_NAME", "下北沢")
# sales.pos_name。SIPOS分と混ざらないよう別名（config.py の name="AirREGI" と一致）。
AIRREGI_POS = os.environ.get("AIRREGI_POS_NAME", "AirREGI")


def run_airregi(sb: Supabase, business_date: str, run_id: str,
                force: bool, headless: bool, store_cache: dict) -> list[StepResult]:
    """Airレジ（下北沢）の会計明細を取り込む。AIRREGI_ID/PW が無ければ何もしない。

    下北沢は「Airレジ＋SIPOS」の2レジ。Airレジ分を pos_name=AirREGI として
    同じ店（NOTIME下北沢店）に合算する。変換は adapters.adapt_airregi（列確定済み）。
    """
    airid = os.environ.get("AIRREGI_ID") or ""
    pw = os.environ.get("AIRREGI_PW") or ""
    if not airid or not pw:
        return []   # 未設定なら静かにスキップ

    from io import StringIO
    import pandas as pd
    import adapters
    from . import airregi_fetch, rows as rows_mod
    started = datetime.now(JST).isoformat()
    print("\n" + "=" * 60)
    print(f"【4】Airレジ 会計明細（下北沢）  対象営業日: {business_date}")
    print("=" * 60)
    try:
        # 接続＝オーナー単位アカウント。取り込みはCSVの店舗名で振り分ける（airregi_common）。
        # 取り込み済み判定はアカウント単位（store_id基準にしない＝全店で1本）。
        if not force and sb.already_succeeded("airregi", business_date, None):
            print("  すでに取り込み済みのためスキップ（--force で解除）。")
            return [StepResult("airregi", "rejected_duplicate", "取り込み済み")]

        csv_text = airregi_fetch.fetch(business_date, headless=headless)
        save_raw(csv_text, "airregi", "下北沢", business_date)
        df_in = pd.read_csv(StringIO(csv_text), dtype=str)
        common = rows_mod.airregi_common(df_in, AIRREGI_POS)   # CSVの店舗名で振り分け
        # [診断] 取得CSVが「どの期間・何行」入っているかを見える化（0件の原因切り分け用）。
        #   ・CSVがそもそも空か／別の期間になっていないか／会計行が残っているか。
        try:
            _date_hist = common["date"].value_counts().sort_index().to_dict() if len(common) else {}
        except Exception:
            _date_hist = {}
        print(f"  [診断] 取得CSV={len(csv_text)}字 / 生データ行={len(df_in)} / 会計明細(adapt後)={len(common)}行")
        print(f"  [診断] CSV先頭180字: {csv_text[:180]!r}")
        print(f"  [診断] adapt後の日付内訳（対象日フィルタ前）: {_date_hist}")
        print(f"  [診断] ← 対象営業日 {business_date} で絞り込みます")
        # 念のため対象営業日だけに絞る（取得CSVに前後日が混ざっても安全に）。
        common = common[common["date"] == business_date].copy()
        common = common[common["date"].notna()]
        common["line_no"] = common.groupby("tx_id").cumcount()

        # 同じ日の AirREGI 売上を（全店ぶん）消してから入れ直す（再実行に強く）。
        sb.delete("sales", {
            "pos_name": f"eq.{AIRREGI_POS}",
            "business_date": f"eq.{business_date}"})
        if len(common) == 0:
            sb.log(run_id=run_id, source="airregi", business_date=business_date,
                   store_id=None, status="no_data", message="売上0",
                   started_at=started)
            print("  売上0（取り込むものなし）。")
            return [StepResult("airregi", "no_data", "売上0")]

        store_id_of = lambda name: sb.get_or_create_store(name, store_cache)
        payload = rows_mod.cashier_rows(common, store_id_of)
        ins, _ = sb.insert_ignore_duplicates(
            "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no")
        txn = len({r["tx_id"] for r in payload})
        ssum = sum(r["sales_in_tax"] for r in payload if r["line_no"] == 0)
        stores = sorted(common["store"].astype(str).unique().tolist())
        print(f"  【Airレジ】{txn}取引 / {int(ssum):,}円 → 追加 {ins}行（店舗: {', '.join(stores[:10])}）")
        sb.log(run_id=run_id, source="airregi", business_date=business_date,
               store_id=None, status="success", rows_fetched=len(payload),
               rows_inserted=ins, message=f"店舗: {', '.join(stores[:20])} / {txn}取引",
               started_at=started)
        return [StepResult("airregi", "success", f"{txn}取引 {int(ssum):,}円", len(payload), ins)]
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        print(f"  ❌ Airレジ取り込み失敗: {detail}")
        if not isinstance(e, EtlError):
            traceback.print_exc()
        sb.log(run_id=run_id, source="airregi", business_date=business_date,
               store_id=None, status="failed", message=detail[:2000], started_at=started)
        return [StepResult("airregi", "failed", detail)]


# ============================================================
# メイン
# ============================================================
# ============================================================
#  LINE（デジテールのLINE配信・友だち数）
# ============================================================
def run_line(sb: Supabase, business_date: str, run_id: str,
             force: bool, headless: bool) -> list[StepResult]:
    """
    デジテールのLINE配信データを取り込む。ログインは来店数と同じ。

    期間は line_fetch が**店舗ごと**に決める。
      まだ1行も無い店 … 全期間（初回の一括取り込みが自動で走る）
      すでにある店   … 直近14日（デジテールが後から数字を直すことがあるため）
    途中で時間切れになっても、残りの店は次の回が全期間を取りにいく。

    best-effort（ここが失敗しても売上・来店の取り込みは止めない）。
    日次ETLの制限時間(60分)を食いつぶさないよう、25分で切り上げる。
    """
    from .line_fetch import run as line_run

    history_from = os.environ.get("DIGITEL_LINE_HISTORY_FROM") or "2019-01-01"
    print(f"\n--- LINE（未取得の店は {history_from} から / 取得済みの店は直近14日）---")
    try:
        # --force のときは、取得済みの店も全期間で取り直す
        line_run(history_from, business_date, headless=headless,
                 history_from=history_from,
                 recent_days=(99999 if force else 14), budget_min=25)
        msg = f"〜{business_date}"
        try:
            sb.log(run_id=run_id, source="line", business_date=business_date,
                   store_id=None, status="success", message=msg)
        except Exception:
            pass
        return [StepResult("line", "success", msg)]
    except Exception as e:                                   # noqa: BLE001
        detail = f"{type(e).__name__}: {e}"
        print(f"  ⚠️ LINEの取り込みに失敗（他は続けます）: {detail[:200]}")
        try:
            sb.log(run_id=run_id, source="line", business_date=business_date,
                   store_id=None, status="failed", message=detail[:2000])
        except Exception:
            pass
        return [StepResult("line", "failed", detail)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NOTIME 日次ETL（cashier売上 / デジテール来店数 → Supabase）"
    )
    parser.add_argument("--date", help="対象の営業日 YYYY-MM-DD（省略時は前日）")
    parser.add_argument("--only",
                        choices=["cashier", "digitel", "pos", "bundle", "smaregi", "airregi", "line"],
                        help="1種類だけ動かす（cashier / digitel / pos=Air/EZ接続 / "
                             "bundle=バンドル名 / smaregi=隠岐 / airregi=下北沢Airレジ / line=LINE）")
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
    if args.only in (None, "bundle"):
        # バンドル名（SALEの名称マスタ）を毎日自動で取得する。
        #   cashierの「バンドル」画面 → 集計(POST /bundle/search) → CSV(GET /bundle/download)
        #   の順で取得し、新しいSALEがあれば bundle_master に自動登録する。
        #   best-effort（失敗しても売上・来店の取り込みは止めない。BEST_EFFORT参照）。
        #   同じ営業日は二度取りに行かない（already_succeeded ガード）。
        results.append(run_bundle_master(sb, business_date, run_id, args.force, headless))
    if args.only in (None, "digitel"):
        results.extend(run_digitel(sb, business_date, run_id, args.force, headless))
    if args.only in (None, "pos"):
        # Air/EZ等の「レジ接続」（store_pos）を巡回して取り込む。未登録なら何もしない。
        from .pos_live import run_live_pos
        results.extend(run_live_pos(sb, business_date, run_id, args.force, headless))
    if args.only in (None, "smaregi"):
        # スマレジ店（隠岐）。SMAREGI_ID/PW が無ければ何もしない。
        results.extend(run_smaregi(sb, business_date, run_id, args.force,
                                   headless, store_cache))
    if args.only in (None, "airregi"):
        # Airレジ店（下北沢の2つ目のレジ）。AIRREGI_ID/PW が無ければ何もしない。
        results.extend(run_airregi(sb, business_date, run_id, args.force,
                                   headless, store_cache))
    if args.only in (None, "line"):
        # デジテールのLINE（配信履歴・友だち数）。来店数と同じログインを使う。
        # 初回は全期間、以降は直近14日。best-effort。
        results.extend(run_line(sb, business_date, run_id, args.force, headless))

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

    # best-effort（失敗しても全体は止めない）:
    #  ・bundle_master（SALEの名称マスタ）
    #  ・レジ接続の各店（ezregi#N / airregi#N）… 1店のログイン不調で全店の
    #    ダッシュボード更新まで止めない。失敗は ingest_log と下の一覧に残る。
    BEST_EFFORT = {"bundle_master", "airregi", "line"}

    def _is_best_effort(r) -> bool:
        return r.name in BEST_EFFORT or "#" in r.name

    failed = [r for r in results if r.status == "failed" and not _is_best_effort(r)]
    warn = [r for r in results if r.status == "failed" and _is_best_effort(r)]
    rejected = [r for r in results if r.status == "rejected_duplicate"]

    # best-effort で握りつぶした失敗や「売上0」は Actions が緑のままなので気づけない。
    # Slack に流して見えるようにする（SLACK_WEBHOOK 未設定なら何もしない）。
    try:
        from .notify import notify_etl_problems
        notify_etl_problems(sb, business_date, results)
    except Exception as _e:                      # noqa: BLE001（通知の失敗でETLは落とさない）
        print(f"  （異常通知の処理に失敗: {type(_e).__name__}: {_e}）")

    if warn:
        names = ", ".join(r.name for r in warn)
        print(f"\n⚠️ 一部はスキップ/失敗しました（best-effort。他の取り込み・ダッシュボードには影響しません）: {names}")
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
