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


def run_cashier_conn_backfill(sb: Supabase, start: str, end: str,
                              headless: bool, store_cache: dict) -> None:
    """『レジ接続』に登録された cashier アカウント（直営Secret以外。例: 天王台）の過去分を
    直営と同じ方式（ログイン→検索→CSV出力(明細)）で一括取り込みする。接続ごとに
    url/id/pw でログインし、店舗名で振り分け（接続にstore_idがあればその店に固定）。"""
    from . import cashier_fetch, pos_sources, rows as rows_mod

    try:
        conns = [c for c in pos_sources.load_connections(sb, only_active=True)
                 if c["pos_type"] == "cashier"]
    except Exception as e:
        print(f"  ℹ️ cashier接続の取得に失敗（スキップ）: {e}")
        return
    if not conns:
        return
    print("\n" + "=" * 60)
    print(f"【1b】cashier(レジ接続) 売上明細  期間: {start} 〜 {end}  接続{len(conns)}件")
    print("=" * 60)

    for c in conns:
        label = f'cashier#{c["id"]}'
        if not c.get("login_pw"):
            print(f"  ⚠️ {label}: パスワード未復号のためスキップ。")
            continue
        try:
            csv_text = cashier_fetch.fetch_range_creds(
                c["login_id"] or "", c["login_pw"] or "", start, end,
                headless=headless)  # cashierは正規の取引一覧URL(既定)を使う
            df = rows_mod.parse_cashier_csv(csv_text, business_date=None)
            if len(df) == 0:
                print(f"  【{label}】対象期間の明細0行。")
                continue
            # 接続＝オーナー単位アカウント。常にCSVの店舗名で振り分ける
            # （store_idに固定しない＝全店が1店に化けるのを防ぐ。詳細は pos_live.py）。
            store_id_of = lambda name: sb.get_or_create_store(name, store_cache)
            payload = rows_mod.cashier_rows(df, store_id_of)
            inserted, duplicate = sb.insert_ignore_duplicates(
                "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no")
            stores = sorted(df["store"].astype(str).unique().tolist())
            dates = sorted(df["date"].unique().tolist())
            txn = len({r["tx_id"] for r in payload})
            ssum = sum(r["sales_in_tax"] for r in payload if r["line_no"] == 0)
            print(f"  【{label}】{dates[0]}〜{dates[-1]}（{len(dates)}日） "
                  f"{txn}取引 / {int(ssum):,}円 → 追加 {inserted}行"
                  f"（店舗: {', '.join(stores[:10])}）")
        except Exception as e:
            print(f"  【{label}】❌ 取得/保存に失敗（スキップ）: {type(e).__name__}: {e}")


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


def run_airregi_backfill(sb: Supabase, start: str, end: str,
                         headless: bool, store_cache: dict) -> None:
    """Airレジ（下北沢）の過去分を一括取り込み。1回のログインで期間まとめて取る。"""
    from io import StringIO
    import pandas as pd
    import adapters
    from . import airregi_fetch, rows as rows_mod
    from .run_daily import AIRREGI_STORE_NAME, AIRREGI_POS

    print("\n" + "=" * 60)
    print(f"【4】Airレジ 会計明細（下北沢）  期間: {start} 〜 {end}")
    print("=" * 60)

    store_id = sb.get_or_create_store(AIRREGI_STORE_NAME, store_cache)
    csv_text = airregi_fetch.fetch_range(start, end, headless=headless)
    df_in = pd.read_csv(StringIO(csv_text), dtype=str)
    common = adapters.adapt_airregi(df_in, AIRREGI_POS, AIRREGI_STORE_NAME)
    common = common[common["date"].notna()].copy()
    common = common[(common["date"] >= start) & (common["date"] <= end)].copy()
    if len(common) == 0:
        print("  ℹ️ 対象期間の明細は0行でした。")
        return
    common["line_no"] = common.groupby("tx_id").cumcount()

    # 期間内の既存 AirREGI 売上を消してから入れ直す（再実行に強く）。
    sb.delete("sales", {
        "store_id": f"eq.{store_id}",
        "pos_name": f"eq.{AIRREGI_POS}",
        "and": f"(business_date.gte.{start},business_date.lte.{end})"})
    payload = rows_mod.cashier_rows(common, lambda name: store_id)
    inserted, duplicate = sb.insert_ignore_duplicates(
        "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no")
    dates = sorted(common["date"].unique().tolist())
    txn = len({r["tx_id"] for r in payload})
    ssum = sum(r["sales_in_tax"] for r in payload if r["line_no"] == 0)
    print(f"  【下北沢/Airレジ】{dates[0]}〜{dates[-1]}（{len(dates)}日） "
          f"{txn}取引 / {int(ssum):,}円")
    print(f"  Supabaseへ保存: 新規 {inserted}行 / 既存で無視 {duplicate}行")


def _month_chunks(start: str, end: str):
    """[start, end] を月境界で区切って (月初, 月末) の並びにする。"""
    from datetime import date as _date, timedelta

    def _d(s):
        y, m, d = map(int, s.split("-")); return _date(y, m, d)

    def _month_end(d):
        nm = _date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
        return nm - timedelta(days=1)

    cur, last = _d(start), _d(end)
    while cur <= last:
        m_end = min(_month_end(cur), last)
        yield cur.isoformat(), m_end.isoformat()
        cur = m_end + timedelta(days=1)


def run_sipos_backfill(sb: Supabase, start: str, end: str,
                       headless: bool, store_cache: dict) -> None:
    """SIPOS（EZレジ）の過去分を『売上報告書→購買情報明細』で作り直す。

    ・取引照会(明細なし)ではなく購買情報明細（＝Si明細形式）を使うので、カテゴリ別も
      出せ、売上・客数・点数が売上報告書に一致する。
    ・SIPOSは直近3か月ぶんしかデータが残らないため、この窓の中を月ごとに取り直す。
    ・同じ店・同じ月の既存行（pos=SIPOS / pos=Si / 旧取引照会）を消してから入れ直すので、
      二重計上（手動Siインポートとの重複・旧取引照会の混入）が解消される。冪等。
    """
    import adapters
    from . import pos_web, pos_sources, si_clean, rows as rows_mod

    print("\n" + "=" * 60)
    print(f"【SIPOS】購買情報明細で作り直し  期間: {start} 〜 {end}")
    print("=" * 60)

    try:
        conns = [c for c in pos_sources.load_connections(sb, only_active=True)
                 if c["pos_type"] == "ezregi"]
    except Exception as e:
        print(f"  ❌ SIPOS接続一覧の取得に失敗: {e}")
        return
    if not conns:
        print("  ℹ️ SIPOS(ezレジ)接続が未登録のためスキップ。")
        return

    # 同じテナント（同一ホスト）に複数の接続があると、購買情報明細は「全店ぶん」を返すため
    # 同じCSVを何度も落とすことになる（重複＝時間の無駄）。ホスト単位で1接続に集約する。
    from urllib.parse import urlparse as _urlparse

    def _host(u: str) -> str:
        u = (u or "").strip()
        if "://" not in u:
            u = "https://" + u
        return (_urlparse(u).netloc or "").lower()

    seen_hosts: set[str] = set()
    uniq: list = []
    for c in conns:
        h = _host(c.get("url"))
        # ログインPWが復号できている接続を優先。ホスト単位で最初の1件だけ使う。
        if h and h in seen_hosts:
            continue
        if h:
            seen_hosts.add(h)
        uniq.append(c)
    if len(uniq) != len(conns):
        print(f"  テナント集約: {len(conns)}接続 → {len(uniq)}テナント（同一ホストは1回のみ取得）")
    conns = uniq

    # 動作確認用：POS_LIMIT=N で先頭N接続だけ、POS_ONLY_STORE=文字 でurl/pos_name一致だけ。
    import os as _os
    only = (_os.environ.get("POS_ONLY_STORE") or "").strip()
    if only:
        conns = [c for c in conns if only in (c.get("url") or "")
                 or only in (c.get("pos_name") or "") or str(c.get("id")) == only]
    lim = (_os.environ.get("POS_LIMIT") or "").strip()
    if lim.isdigit() and int(lim) > 0:
        conns = conns[:int(lim)]
    print(f"  対象接続: {len(conns)}件")

    # この作り直しで置き換える pos_name（重複の元をまとめて掃除する）。
    stale_pos = {"SIPOS", "Si"}
    for c in conns:
        if c.get("pos_name"):
            stale_pos.add(c["pos_name"])
    pos_list = ",".join(sorted(stale_pos))

    # 全接続を対象にする通常実行では、窓内の旧SIPOS(自動)行を先に全店ぶん一掃する。
    #   → 過去の誤集計・店舗混線（改名前後の別名に総額が重複コピーされた等）を確実に除去。
    #   ※ POS_LIMIT / POS_ONLY_STORE で絞った動作確認時は、他店を消さないよう一掃しない。
    if not only and not (lim.isdigit() and int(lim) > 0):
        try:
            for pn in sorted({p for p in stale_pos if p != "Si"}):
                sb.delete("sales", {
                    "pos_name": f"eq.{pn}",
                    "and": f"(business_date.gte.{start},business_date.lte.{end})"})
            print(f"  🧹 窓内の旧SIPOS(自動)行を一掃しました（{start}〜{end}）。")
        except Exception as e:
            print(f"  ⚠️ 旧SIPOS行の一掃に失敗: {type(e).__name__}: {e}")

    total_ins = 0
    for c in conns:
        label = f'ezregi#{c["id"]}'
        if not c.get("login_pw"):
            print(f"  ⚠️ {label}: パスワード未復号のためスキップ。")
            continue
        for m_start, m_end in _month_chunks(start, end):
            ym = m_start[:7]
            try:
                csv_text = pos_web.fetch(c["url"], c["login_id"], c["login_pw"],
                                         m_start, "ezregi", label=f"{label}/{ym}",
                                         headless=headless, end_date=m_end)
                if not (csv_text or "").strip():
                    print(f"  【{label}】{ym}: 明細0件（休業・売上なし/データ保持期間外）。")
                    continue
                df_in = _read_si_month(csv_text)
                if df_in is None or len(df_in) == 0:
                    print(f"  【{label}】{ym}: 取り込める明細行なし。")
                    continue
                _cols = list(df_in.columns)
                df_in = si_clean.normalize_si_datetime(df_in)
                common = adapters.adapt_ezregi(df_in, c["pos_name"] or "SIPOS")

                # 店の対応付け：購買情報明細は必ず「全店ぶん」が入っているので、接続の
                # store_id は使わず“CSVの店舗名で振り分ける”（手動インポートと同じ）。
                #   ※ 接続=1店に固定すると、テナント全店の売上が1店に丸ごと入る誤りになる。
                # adapt_ezregi と同じ規則で残した行から店名を作り、ラベルずれの起きない
                # 「位置対応」で割り当てる（reindexだとラベル不一致でNaN混入・誤割当が起きる）。
                kept = df_in[df_in["店舗名"].fillna("").astype(str).str.strip() != ""]
                names = si_clean.si_store_names(kept["店舗名"]).astype(str)
                if len(names) == len(common):
                    common["store"] = names.to_numpy()
                else:
                    print(f"  【{label}】{ym}: ⚠️ 行数不一致(kept={len(names)}/common={len(common)})"
                          "→ ラベル対応にフォールバック")
                    common["store"] = si_clean.si_store_names(df_in["店舗名"]).reindex(common.index)
                store_id_of = lambda name: sb.get_or_create_store(name, store_cache)

                common = common[common["date"].notna()].copy()
                common = common[(common["date"] >= m_start) & (common["date"] <= m_end)].copy()
                # 店名が空/NaNの行は捨てる（誤店舗への割当・集計時のjoin失敗を防ぐ）。
                common = common[common["store"].notna()].copy()
                common["store"] = common["store"].astype(str)
                common = common[~common["store"].isin(["", "nan", "None"])].copy()
                if len(common) == 0:
                    print(f"  【{label}】{ym}: 期間内の明細0件。")
                    continue
                common["line_no"] = common.groupby("tx_id").cumcount()
                payload = rows_mod.cashier_rows(common, store_id_of)

                # 影響する店だけ、対象月の既存行（SIPOS/Si/旧取引照会）を消してから入れ直す。
                store_ids = sorted({r["store_id"] for r in payload})
                if store_ids:
                    sb.delete("sales", {
                        "store_id": f"in.({','.join(str(i) for i in store_ids)})",
                        "pos_name": f"in.({pos_list})",
                        "and": f"(business_date.gte.{m_start},business_date.lte.{m_end})"})
                ins, _dup = sb.insert_ignore_duplicates(
                    "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no")
                total_ins += ins
                txn = len({(r["store_id"], r["tx_id"]) for r in payload})
                ssum = sum(r["sales_in_tax"] for r in payload if r["line_no"] == 0)
                stores = sorted({str(r) for r in common["store"].tolist()
                                 if str(r) not in ("", "nan", "None")})
                print(f"  【{label}】{ym}: {txn}取引 / {int(ssum):,}円 → 追加 {ins}行"
                      f"（店舗: {', '.join(stores[:12])}）")
            except Exception as e:
                import traceback as _tb
                print(f"  【{label}】{ym}: ❌ 取得/保存に失敗（スキップ）: "
                      f"{type(e).__name__}: {e}")
                try:
                    print(f"    列={_cols}")
                    print(f"    先頭行={df_in.iloc[0].to_dict()}")
                except Exception:
                    pass
                _tb.print_exc()

    # 旧「取引照会」から取得した行を全期間から掃除する（ユーザー要望）。
    #   取引照会は明細が無く line_category='（明細なし）' で入っている。購買情報明細
    #   （新・正）は実カテゴリ名が入るので、pos=SIPOS かつ （明細なし） が旧取引照会の
    #   目印になる（digitelの明細なしは pos_name が別なので巻き込まない）。
    try:
        for pn in sorted({c.get("pos_name") or "SIPOS" for c in conns} | {"SIPOS"}):
            sb.delete("sales", {
                "pos_name": f"eq.{pn}",
                "line_category": 'in.("（明細なし）","明細なし")'})
        print("  🧹 旧・取引照会データ（明細なし）を削除しました。")
    except Exception as e:
        print(f"  ⚠️ 旧・取引照会データの削除に失敗: {type(e).__name__}: {e}")

    print(f"  ✅ SIPOS backfill 完了（合計 追加 {total_ins}行）")


def _read_si_month(csv_text: str):
    """購買情報明細CSVを DataFrame に。見出し（店舗コード…レシートNo）から読む。"""
    import io as _io
    import pandas as pd
    lines = csv_text.splitlines()
    hdr = next((i for i, ln in enumerate(lines)
                if "店舗コード" in ln and "レシートNo" in ln), None)
    if hdr is None:
        return None
    df = pd.read_csv(_io.StringIO("\n".join(lines[hdr:])), dtype=str,
                     engine="python", on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]
    return df[df["店舗名"].astype(str).str.strip() != ""]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NOTIME 過去分の一括取り込み（backfill）"
    )
    parser.add_argument("--from", dest="date_from", default=DEFAULT_FROM,
                        help=f"開始日 YYYY-MM-DD（既定 {DEFAULT_FROM}）")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="終了日 YYYY-MM-DD（既定は今日）")
    parser.add_argument("--only", choices=["cashier", "digitel", "smaregi", "airregi", "sipos"],
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
        try:
            run_cashier_conn_backfill(sb, start, end, headless, store_cache)
        except Exception as e:
            failed = True
            print(f"  ❌ cashier(レジ接続) backfill 失敗: {type(e).__name__}: {e}")

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

    if args.only in (None, "airregi"):
        try:
            run_airregi_backfill(sb, start, end, headless, store_cache)
        except Exception as e:
            failed = True
            print(f"  ❌ Airレジ backfill 失敗: {type(e).__name__}: {e}")

    # SIPOS は既定の一括(None)には含めない（3か月保持・作り直し系のため明示指定で動かす）。
    if args.only == "sipos":
        try:
            run_sipos_backfill(sb, start, end, headless, store_cache)
        except Exception as e:
            failed = True
            print(f"  ❌ SIPOS backfill 失敗: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    if failed:
        print("⚠️ 一部が失敗しました。上のログを確認してください。")
        return 1
    print("✅ 過去分の一括取り込みが完了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
