"""ドライラン検証：各POSアカウントのCSVを取得し「店舗名→どのstore_idに入るか」だけを表示する。

書き込みは一切しない（sales に insert/delete しない）。新しい name-split 仕様（接続＝オーナー
単位／常に店舗名で振り分け）が、既存の店(id)へ正しく寄るか、新規店を誤作成しないかを確認する。

  対象: cashier（store_pos の各接続・PW復号できるもの）＋ Airレジ（環境変数 AIRREGI_ID/PW）
  期間: 環境変数 PROBE_FROM / PROBE_TO（既定 2026-08-01〜2026-08-07）
"""
from __future__ import annotations
import io
import os
import traceback

import pandas as pd

from etl.settings import store_key, canonical_store_name
from etl.supabase_client import Supabase

FROM = os.environ.get("PROBE_FROM", "2026-08-01")
TO = os.environ.get("PROBE_TO", "2026-08-07")


def build_key_map(sb: Supabase) -> dict:
    """現在の stores を store_key で引ける辞書に（id が付いた行のみ）。"""
    rows = sb.select("stores", {"select": "id,name", "order": "id"})
    by_key: dict[str, tuple] = {}
    for s in rows:
        if s.get("id") is None:
            continue
        by_key.setdefault(store_key(s["name"]), (s["id"], s["name"]))
    return by_key


def show_split(title: str, names, by_key: dict) -> None:
    print(f"\n===== {title} =====")
    uniq = sorted({str(n).strip() for n in names if str(n).strip()})
    if not uniq:
        print("  （店舗名を取得できませんでした／0件）")
        return
    ok, new = 0, 0
    for raw in uniq:
        k = store_key(raw)
        hit = by_key.get(k)
        if hit:
            ok += 1
            print(f"  ✅「{raw}」→ key={k} → id={hit[0]} {hit[1]}")
        else:
            new += 1
            print(f"  ★「{raw}」→ key={k} → 新規作成されます（エイリアス要検討）"
                  f" / canonical={canonical_store_name(raw)}")
    print(f"  → 既存店に一致 {ok} / 新規作成される {new}")


def probe_cashier(sb: Supabase, by_key: dict) -> None:
    from etl.pos_sources import load_connections
    from etl import cashier_fetch, rows as rows_mod
    try:
        conns = [c for c in load_connections(sb, only_active=True)
                 if c["pos_type"] == "cashier"]
    except Exception as e:
        print(f"\n[cashier] 接続取得失敗: {e}")
        return
    print(f"\n[cashier] 接続 {len(conns)}件")
    for c in conns:
        label = f'cashier#{c["id"]} store_id={c.get("store_id")} login={c.get("login_id")}'
        if not c.get("login_pw"):
            print(f"  ⚠️ {label}: PW未復号のためスキップ")
            continue
        try:
            csv_text = cashier_fetch.fetch_range_creds(
                c["login_id"] or "", c["login_pw"] or "", FROM, TO, headless=True)
            df = rows_mod.parse_cashier_csv(csv_text, business_date=None)
            show_split(label, df["store"].tolist() if len(df) else [], by_key)
        except Exception as e:
            print(f"  ❌ {label}: {type(e).__name__}: {e}")
            traceback.print_exc()


def probe_airregi(by_key: dict) -> None:
    airid = os.environ.get("AIRREGI_ID") or ""
    pw = os.environ.get("AIRREGI_PW") or ""
    if not (airid and pw):
        print("\n[Airレジ] AIRREGI_ID/PW 未設定のためスキップ")
        return
    from etl import airregi_fetch
    try:
        csv_text = airregi_fetch.fetch_range(FROM, TO, headless=True)
        df = pd.read_csv(io.StringIO(csv_text), dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        print(f"\n[Airレジ] CSV列: {list(df.columns)[:20]}")
        col = "店舗名" if "店舗名" in df.columns else None
        if col is None:
            print("  ⚠️ 『店舗名』列が見つかりません。列名を確認してください。")
            return
        # 「会計」だけに絞って店舗名の実値を見る
        if "取引種別" in df.columns:
            df = df[df["取引種別"].astype(str).str.strip() == "会計"]
        show_split("Airレジ 店舗名（生値→解決先）", df[col].tolist(), by_key)
    except Exception as e:
        print(f"\n[Airレジ] ❌ {type(e).__name__}: {e}")
        traceback.print_exc()


def main() -> int:
    print(f"=== POS 振り分けドライラン（{FROM}〜{TO}／無書込） ===")
    sb = Supabase()
    by_key = build_key_map(sb)
    print(f"現在の stores（id付き・store_key数）: {len(by_key)}")
    probe_cashier(sb, by_key)
    probe_airregi(by_key)
    print("\n※ これは表示のみ。sales には一切書き込んでいません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
