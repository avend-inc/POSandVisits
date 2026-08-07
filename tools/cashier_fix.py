"""cashierの誤ピンを是正する。

原因：store_pos の接続 id=31（admin@avend.co.jp＝全店を1ファイルに出す親アカウント）が
store_id=50（長野）に固定ピンされていた。ETLのcashierは接続にstore_idがあるとCSVの
店舗名を無視して全明細をその1店に寄せるため、親アカウントの全店売上が長野に化けた。

是正：
  1) 接続 id=31 の store_id を NULL にする（＝店舗名で振り分け＝山形/いわき/福井へ正しく分配）
  2) 長野(store_id=50)の cashier 明細を削除（開店前で実売上ゼロのはず。誤集計ぶんを除去）
     → 後で cashier を name-split で再取り込みすれば 1/2/3 に正しく入る（重複はdedupで無害）

  実行: python -m tools.cashier_fix          … 現状確認のみ
        APPLY=1 python -m tools.cashier_fix  … 是正を適用
"""
from __future__ import annotations
import json
import os
import re

import requests

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""
APPLY = os.environ.get("APPLY") == "1"
CONN_ID = int(os.environ.get("CONN_ID") or "31")   # 誤ピンの接続
BAD_STORE = int(os.environ.get("BAD_STORE") or "50")  # 誤って寄せられた店(長野)


def run_sql(sql: str):
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": sql}, timeout=60)
    if r.status_code >= 400:
        raise SystemExit(f"SQL失敗 HTTP {r.status_code}: {r.text[:400]}\n--SQL--\n{sql}")
    return r.json()


def main() -> int:
    print(f"接続 id={CONN_ID} / 誤集計店 store_id={BAD_STORE}")
    print("\n現状の接続:")
    print(json.dumps(run_sql(
        f"select id,store_id,pos_type,login_id,active from public.store_pos where id={CONN_ID}"),
        ensure_ascii=False))
    print("\n長野(店50)の cashier 明細:")
    print(json.dumps(run_sql(
        f"select count(*) rows, count(distinct tx_id) txn, min(business_date) f, max(business_date) t "
        f"from public.sales where store_id={BAD_STORE} and pos_name='cashier'"), ensure_ascii=False))

    if not APPLY:
        print("\n【確認のみ】APPLY=1 で ①接続のstore_idをNULL ②長野のcashier明細を削除 を実行します。")
        return 0

    print("\n【適用①】接続の store_id を NULL に（店舗名で振り分けへ）…")
    run_sql(f"update public.store_pos set store_id=null where id={CONN_ID};")
    print("【適用②】長野(店50)の cashier 明細を削除…")
    d = run_sql(f"delete from public.sales where store_id={BAD_STORE} and pos_name='cashier' returning tx_id;")
    print(f"  削除行数: {len(d) if isinstance(d, list) else d}")
    print("\n事後確認:")
    print(json.dumps(run_sql(
        f"select id,store_id,pos_type,login_id from public.store_pos where id={CONN_ID}"), ensure_ascii=False))
    print(json.dumps(run_sql(
        f"select count(*) rows from public.sales where store_id={BAD_STORE} and pos_name='cashier'"),
        ensure_ascii=False))
    print("\n✅ 是正完了。次に cashier を再取り込み（name-split）してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
