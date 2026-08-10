"""
data.json の日別行に「付くべき項目が本当に付いているか」を確かめる自己診断。

    python -m tools.check_export_fields

【なぜ必要か】
  集計側（build_data）で項目を作っても、日別行に差し込むのを書き忘れると
  data.json に出ない。画面はその項目の有無で表示を切り替えているため、
  「機能を入れたのに画面に何も出ない」という形で初めて気づくことになる。
  （実際に exU/exM・vm でそれをやった。）
  Supabase には接続せず、偽のデータを流して出力の形だけを確かめるので、
  いつでも安全に実行できる。
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import export_dashboard as ed  # noqa: E402

# 下北沢(4) = SIPOS(無人) + AirREGI(有人) の2レジ運用／山形(1) = cashierだけの1レジ店
_SALES: list[dict] = []


def _tx(date: str, sid: int, pos: str, txid: str, in_tax: int, ex_tax: int) -> None:
    _SALES.append({"business_date": date, "store_id": sid, "pos_name": pos, "tx_id": txid,
                   "sales_in_tax": in_tax, "sales_ex_tax": ex_tax, "tx_qty": 1,
                   "line_category": "トップス", "line_amount": ex_tax, "line_qty": 1,
                   "bundle_code": None, "is_parent": None})


_tx("2026-08-08", 4, "SIPOS", "S1", 5500, 5000)
_tx("2026-08-08", 4, "AirREGI", "A1", 11000, 10000)
_tx("2026-08-09", 4, "SIPOS", "S2", 2200, 2000)
_tx("2026-08-09", 4, "AirREGI", "A2", 22000, 20000)
_tx("2026-08-09", 1, "cashier", "C1", 3300, 3000)

_VISITS = [
    {"business_date": "2026-08-08", "store_id": 4, "source": "digitel", "visitors": 12},
    {"business_date": "2026-08-09", "store_id": 4, "source": "digitel", "visitors": 9},
    {"business_date": "2026-08-08", "store_id": 4, "source": "staffed", "visitors": 20},
    {"business_date": "2026-08-09", "store_id": 1, "source": "digitel", "visitors": 30},
]


class _FakeSB:
    """Supabaseの代わり。build_data が投げてくる select にだけ答える。"""

    def select(self, table: str, params: dict) -> list[dict]:
        if params.get("offset") not in (None, "0"):
            return []                      # ページングは1ページで終わり
        if table == "stores":
            return [{"id": 1, "name": "NOTIME山形店", "ownership": "直営",
                     "visible": True, "kpi_visitors": True},
                    {"id": 4, "name": "NOTIME下北沢店", "ownership": "直営",
                     "visible": True, "kpi_visitors": False}]
        if table == "store_pos":
            return [{"store_id": 4, "pos_type": "ezregi", "pos_name": "SIPOS"},
                    {"store_id": 4, "pos_type": "airregi", "pos_name": "AirREGI"}]
        if table == "sales":
            return _SALES
        if table == "visits":
            assert params.get("source") == "in.(digitel,staffed)", \
                f"visits の取得条件が想定と違います: {params.get('source')}"
            return _VISITS
        return []


def main() -> int:
    with contextlib.redirect_stdout(io.StringIO()):     # 診断出力は捨てる
        data = ed.build_data(_FakeSB())
    rows = {(r["d"], r["s"]): r for r in data["daily"]}
    shimokita8 = rows[("2026-08-08", 4)]
    shimokita9 = rows[("2026-08-09", 4)]
    yamagata = rows[("2026-08-09", 1)]

    ng = 0

    def chk(label: str, got, want) -> None:
        nonlocal ng
        good = got == want
        if not good:
            ng += 1
        print(f"  {'OK ' if good else '❌ '}{label}: {got}（期待 {want}）")

    print("data.json の日別行チェック")
    chk("2レジ店に無人の内訳(exU)が付く", shimokita8.get("exU"), 5000)
    chk("2レジ店に有人の内訳(exM)が付く", shimokita8.get("exM"), 10000)
    chk("内訳の合計が税抜売上と一致(8/8)",
        shimokita8.get("exU", 0) + shimokita8.get("exM", 0), shimokita8["ex"])
    chk("内訳の合計が税抜売上と一致(8/9)",
        shimokita9.get("exU", 0) + shimokita9.get("exM", 0), shimokita9["ex"])
    chk("有人来店の手入力(vm)が付く", shimokita8.get("vm"), 20)
    chk("入力が無い日には vm を付けない", "vm" in shimokita9, False)
    chk("無人の来店(v)は自動取得ぶんのまま", shimokita8.get("v"), 12)
    chk("1レジ店には内訳を付けない", ("exU" in yamagata or "exM" in yamagata), False)

    if ng:
        print(f"\n❌ {ng}件おかしいところがあります。")
        return 1
    print("\n✅ すべて期待どおりです。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
