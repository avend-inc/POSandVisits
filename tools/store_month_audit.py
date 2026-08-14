"""
店舗×月の売上を細かく監査する（数字が合わない原因の切り分け用）

目的:
  「いわきの7月 税抜売上が変（2,166,947のはず）」のような、店舗の数字が
  期待値と合わない時に、元データ(sales)を export_dashboard と同じロジックで
  積み上げて内訳を見せる。重複店（店ID割れ）・小物/レジ袋の混入・カテゴリの
  偏りを一目で判断できるようにする。

使い方:
  python -m tools.store_month_audit いわき 2026-07
  python -m tools.store_month_audit SELFURUGI徳島沖浜 2026-08
  python -m tools.store_month_audit 下北沢 all     ← 月に all で「全期間の棚卸し」

  all のときは1か月ぶんの内訳ではなく、月ごとの一覧を出す。
    ・いつからデータが入っているか
    ・どのレジ（pos_name）から入っているか。2レジの店は、片方だけ入っている
      月がひと目で分かる（下北沢＝無人SIPOS＋有人Airレジ）
    ・途中で抜けている月はどこか
  過去データの取り込み漏れを探すとき用。

出るもの（stdout）:
  1) 店名にマッチする店舗（同名・重複の有無＝店ID割れの確認）
  2) 各店ごとに、その月の:
       ・税抜売上(ex, 伝票重複排除)／税込売上(in)／伝票数(tx)
       ・商品販売数(it, レジ袋/クーポン除外)  … 小物込み / 小物除外 の両方
       ・レジ袋・クーポン・小物 の点数と金額
       ・カテゴリ別（点数・税抜金額）上位
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.settings import load_dotenv, store_key  # noqa: E402
from etl.supabase_client import Supabase  # noqa: E402

PAGE = 1000
EXCLUDE = {"レジ袋", "クーポン"}      # export_dashboard と同じ（点数・カテゴリから除外）
NOCAT = {"（明細なし）", "明細なし"}


def _month_span(ym: str) -> tuple[str, str]:
    y, m = int(ym[:4]), int(ym[5:7])
    start = f"{y:04d}-{m:02d}-01"
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = f"{ny:04d}-{nm:02d}-01"
    return start, end


def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _rows(sb: Supabase, table: str, params: dict) -> list[dict]:
    out, offset = [], 0
    while True:
        p = dict(params); p.update({"limit": str(PAGE), "offset": str(offset)})
        chunk = sb.select(table, p)
        out.extend(chunk)
        if len(chunk) < PAGE:
            break
        offset += PAGE
    return out


def audit_store(sb: Supabase, sid: int, name: str, start: str, end: str) -> None:
    rows = _rows(sb, "sales", {
        "select": "business_date,store_id,pos_name,tx_id,sales_ex_tax,sales_in_tax,"
                  "line_category,line_qty,line_amount",
        "and": f"(business_date.gte.{start},business_date.lt.{end})",
        "store_id": f"eq.{sid}",
        "order": "business_date",
    })
    tx_seen = set()
    ex = in_ = 0.0
    it_all = it_no_kobutsu = 0.0
    cat_qty = defaultdict(float); cat_amt = defaultdict(float)
    special = defaultdict(lambda: [0.0, 0.0])   # name -> [qty, amount]
    for r in rows:
        txkey = (r["store_id"], r.get("pos_name") or "", r["tx_id"])
        if txkey not in tx_seen:
            tx_seen.add(txkey)
            ex += _num(r["sales_ex_tax"]); in_ += _num(r["sales_in_tax"])
        c = (r.get("line_category") or "その他").strip() or "その他"
        qty = _num(r["line_qty"]); amt = _num(r["line_amount"])
        if c in EXCLUDE:
            special[c][0] += qty; special[c][1] += amt
            continue
        it_all += qty
        if c != "小物":
            it_no_kobutsu += qty
        else:
            special["小物"][0] += qty; special["小物"][1] += amt
        if c in NOCAT:
            continue
        cat_qty[c] += qty; cat_amt[c] += amt

    print(f"\n===== 【{name}】(store_id={sid}) {start}〜{end}未満 =====")
    print(f"  税抜売上(ex)      : {ex:>14,.0f} 円")
    print(f"  税込売上(in)      : {in_:>14,.0f} 円")
    print(f"  伝票数(tx)        : {len(tx_seen):>14,}")
    print(f"  商品販売数(小物込み): {it_all:>12,.0f} 点")
    print(f"  商品販売数(小物除外): {it_no_kobutsu:>12,.0f} 点  ← ③の希望仕様")
    for k in ("小物", "レジ袋", "クーポン"):
        q, a = special.get(k, [0.0, 0.0])
        print(f"    {k:<6}: {q:>10,.0f} 点 / 税抜 {a:>12,.0f} 円")
    print("  --- カテゴリ別（税抜金額 上位10）---")
    for c, a in sorted(cat_amt.items(), key=lambda x: -x[1])[:10]:
        print(f"    {c:<16} {cat_qty[c]:>8,.0f} 点 / {a:>12,.0f} 円")


def audit_all(sb: Supabase, sid: int, name: str) -> None:
    """その店の全期間を月ごとに棚卸しする（どこから入っていて、どこが抜けているか）。"""
    rows = _rows(sb, "sales", {
        "select": "business_date,store_id,pos_name,tx_id,sales_ex_tax",
        "store_id": f"eq.{sid}",
        "order": "business_date",
    })
    print(f"\n===== 【{name}】(store_id={sid}) 全期間の棚卸し =====")
    if not rows:
        print("  この店の sales に1行もありません。")
        return
    # 月 × レジ ごとに、伝票を重複排除しながら税抜売上と日数を集める
    per = defaultdict(lambda: defaultdict(lambda: {"ex": 0.0, "tx": set(), "days": set()}))
    pos_names = set()
    for r in rows:
        d = r["business_date"]
        pos = (r.get("pos_name") or "（無記名）").strip() or "（無記名）"
        pos_names.add(pos)
        e = per[d[:7]][pos]
        key = (pos, r["tx_id"])
        if key not in e["tx"]:
            e["tx"].add(key)
            e["ex"] += _num(r["sales_ex_tax"])
        e["days"].add(d)

    first, last = rows[0]["business_date"], rows[-1]["business_date"]
    print(f"  いちばん古い日: {first}   いちばん新しい日: {last}")
    print(f"  レジ(pos_name): {'、'.join(sorted(pos_names))}")
    poss = sorted(pos_names)
    head = "  " + "月".rjust(9) + "".join(f"{p[:10]:>16}" for p in poss) + f"{'合計':>16}{'営業日':>8}"
    print("\n" + head)
    print("  " + "-" * (len(head) - 2))
    # 最初の月から最後の月まで、データが無い月も飛ばさずに並べる
    y, m = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    gaps = []
    while (y, m) <= (ly, lm):
        ym = f"{y:04d}-{m:02d}"
        e = per.get(ym)
        line = "  " + ym.rjust(9)
        total = 0.0
        days: set = set()
        for p in poss:
            v = e[p]["ex"] if (e and p in e) else None
            total += v or 0.0
            if e and p in e:
                days |= e[p]["days"]
            line += (f"{v:>15,.0f}円" if v is not None else f"{'—':>16}")
        # 1行も無い月は 0円ではなく「—」。0円は「開けたのに売れなかった」に読める
        line += (f"{total:>15,.0f}円{len(days):>7}日" if e else f"{'—':>16}{'—':>8}")
        print(line)
        if not e:
            gaps.append(ym)
        if (m := m + 1) > 12:
            m, y = 1, y + 1
    if gaps:
        print(f"\n  ⚠ データが1行も無い月: {'、'.join(gaps)}")
    # 片方のレジしか入っていない月（2レジ運用の店で、取り込み漏れが見える）
    if len(poss) > 1:
        partial = [ym for ym, e in sorted(per.items()) if len(e) < len(poss)]
        if partial:
            print(f"  ⚠ 一部のレジしか入っていない月: {'、'.join(partial)}")
        else:
            print("  すべての月に、全部のレジのデータが入っています。")


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 3:
        print("使い方: python -m tools.store_month_audit <店名の一部> <YYYY-MM|all>")
        return 2
    needle, ym = sys.argv[1], sys.argv[2]
    if ym.lower() in ("all", "*", "全期間"):
        sb = Supabase()
        stores = sb.select("stores", {"select": "*", "order": "id"})
        nk = store_key(needle)
        matches = [s for s in stores
                   if needle in (s["name"] or "") or store_key(s["name"] or "") == nk
                   or nk in store_key(s["name"] or "")]
        print(f"===== 店舗監査: 「{needle}」× 全期間 =====")
        print(f"マッチした店舗 {len(matches)}件（複数なら店ID割れの疑い）:")
        for s in matches:
            print(f"  id={s['id']}  {s['name']}  (区分={s.get('ownership') or '直営'})")
        if not matches:
            print("  該当なし。店名の一部を変えて再実行してください。")
            return 0
        for s in matches:
            audit_all(sb, s["id"], s["name"])
        print("\n===== 監査おわり =====")
        return 0
    start, end = _month_span(ym)
    sb = Supabase()
    stores = sb.select("stores", {"select": "*", "order": "id"})
    nk = store_key(needle)
    matches = [s for s in stores
               if needle in (s["name"] or "") or store_key(s["name"] or "") == nk
               or nk in store_key(s["name"] or "")]
    print(f"===== 店舗監査: 「{needle}」× {ym} =====")
    print(f"マッチした店舗 {len(matches)}件（複数なら店ID割れの疑い）:")
    for s in matches:
        print(f"  id={s['id']}  {s['name']}  (区分={s.get('ownership') or '直営'}, "
              f"digitel_slug={s.get('digitel_slug') or '—'})")
    if not matches:
        print("  該当なし。店名の一部を変えて再実行してください。")
        return 0
    for s in matches:
        audit_store(sb, s["id"], s["name"], start, end)
    print("\n===== 監査おわり =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
