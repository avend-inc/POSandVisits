"""ネット不要のパイプライン実証（§9.1.1面積 / §2ゲート / §3スコア / §3.3立地 / §9出力）。

この環境（データセンター・外部取得不可）でも、連合隊の実取得以外は全部動くことを示す。
教師データ（§1）に近い合成物件でゲート・スコア・ランクの妥当性を確認できる。

  python -m src.cli selftest
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import area as area_mod
from .db import Store
from .models import RawListing
from .run import build_properties, dedupe
from .reporters import HtmlReporter, TsvReporter
from .reporters.base import RunSummary

ROOT = Path(__file__).resolve().parent.parent


def _raw(name, addr, area, rent, park, floor, road=True,
         dep="2ヶ月", key="1ヶ月", brk="1ヶ月", city="大分市", pref="大分県"):
    return RawListing(
        source_domain="fudosanlist.cbiz.ne.jp",
        detail_url=f"https://fudosan.cbiz.ne.jp/detailPage/rent/900/{abs(hash(name)) % 99999}/",
        prefecture=pref, city=city, name=name, address=addr,
        area_text=area, rent_text=rent, parking_text=park, floor_text=floor,
        deposit_text=dep, key_money_text=key, brokerage_text=brk,
        roadside_flag=road,
    )


# 教師データ(§1)に着想を得た合成物件。ゲート・スコア・§9.1.1を一通り踏む。
SAMPLES_OITA = [
    _raw("福井型・大型ロードサイド", "大分市明野東1-1", "69.9坪", "賃料 34.8万円", "駐車場 15台", "1階"),
    _raw("伊予松前型・SC隣接", "大分市西大道2-2", "39.3坪", "20万円", "5台", "1階"),
    _raw("山形型・国道沿い", "大分市大字森町3-3", "83㎡", "20万円", "5台", "1階"),
    _raw("2階物件（要目視）", "大分市中央町4-4", "30坪", "15万円", "3台", "2階"),
    _raw("駐車場未記載（要確認）", "大分市青葉町5-5", "40坪", "22万円", None, "1階"),
    _raw("面積単位なし→除外", "大分市府内町6-6", "150", "20万円", "4台", "1階"),
    _raw("坪単価オーバー→除外", "大分市要町7-7", "25坪", "40万円", "3台", "1階"),
    _raw("帖表記（換算・小型で除外）", "大分市金池町8-8", "50帖", "12万円", "3台", "1階"),
]
# 富士市（§6.0.1 warn）: A級スペックでも商圏未確定でランクAは出さない
SAMPLES_FUJI = [
    _raw("福井型スペック（富士・商圏未確定）", "富士市中央町1-1", "69.9坪", "34.8万円", "15台", "1階",
         city="富士市", pref="静岡県"),
]


def _area_checks():
    print("=== §9.1.1 面積パーサの単位判定 ===")
    for t in ["32.5坪", "107.4㎡", "1,024 m2", "50帖", "20畳", "150", "83.0平米"]:
        try:
            sqm, tsubo = area_mod.parse_area(t)
            print(f"  {t!r:>12} -> {tsubo:.1f}坪 / {sqm:.1f}㎡")
        except area_mod.ParseError as e:
            print(f"  {t!r:>12} -> パース失敗（推測で埋めない）: {e}")
    print()


def _linkcheck_checks():
    from . import linkcheck
    print("=== リンク生存判定（ソフト404の本文検出。ネット不要のデモ）===")
    dead_body = '<html><body><h1>申し訳ありません。</h1><p>該当の物件が存在しません。</p>© 2026 RALSNET.</body></html>'
    alive_body = '<html><body><h1>明野ロードサイド店舗</h1><p>賃料 34.8万円／69.9坪</p></body></html>'
    print(f"  掲載終了ページ -> is_dead={linkcheck.is_dead_html(dead_body)}  （True＝除外）")
    print(f"  通常の物件ページ -> is_dead={linkcheck.is_dead_html(alive_body)}  （False＝生存）")
    print()


class _FakeFetcher:
    """ネット無しで verify_links を実演するための疑似fetcher。dead_urls だけ掲載終了を返す。"""
    def __init__(self, dead_urls):
        self.dead = set(dead_urls)
    def status_and_body(self, url, timeout=20):
        if url in self.dead:
            return 200, "<html>申し訳ありません。該当の物件が存在しません。</html>"
        return 200, "<html>物件詳細 賃料 家賃 駐車場</html>"


def main():
    _area_checks()
    _linkcheck_checks()

    today = date.today()
    cities = {
        "大分市": {"name": "大分市", "pref": "大分県", "pref_roma": "oita",
                 "jis": "44201", "main_station": "大分駅", "priority": 2, "warn": True},
        "富士市": {"name": "富士市", "pref": "静岡県", "pref_roma": "shizuoka",
                 "jis": "22210", "main_station": "富士駅", "priority": 2, "warn": True},
    }
    all_props = []
    all_notes = []
    props, notes = build_properties(SAMPLES_OITA, cities["大分市"], today)
    all_props += props; all_notes += notes
    props, notes = build_properties(SAMPLES_FUJI, cities["富士市"], today)
    all_props += props; all_notes += notes
    all_props = dedupe(all_props)

    # リンク生存確認の実演: 生存物件のうち1件を「掲載終了」に見立てて除外させる。
    from .run import verify_links
    summary = RunSummary()
    victim = next((p for p in all_props if p.rank != "除外"), None)
    fake = _FakeFetcher(dead_urls=[victim.detail_url] if victim else [])
    verify_links(fake, all_props, summary)
    if victim:
        print(f"※ リンク生存確認デモ: 「{victim.name}」を掲載終了とみなし除外（リンク切れ）\n")

    print("=== §2ゲート / §3スコア / §3.3立地 の結果 ===")
    hdr = f"{'物件名':<26}{'坪数':<20}{'家賃':<11}{'駐車':<6}{'階':<5}{'ゲート':<10}{'点':<5}{'ランク':<6}AI評価/フラグ"
    print(hdr); print("-" * 120)
    for p in sorted(all_props, key=lambda x: ({'A':0,'B':1,'C':2,'要確認':3,'除外':4}[x.rank], -(x.score or 0))):
        area = f"{p.area_tsubo:.1f}坪({p.area_sqm:.1f}㎡)" if p.area_tsubo else "要確認"
        rent = f"¥{p.rent_monthly:,}" if p.rent_monthly else "要相談"
        park = f"{p.parking_slots}台" if p.parking_slots is not None else "—"
        floor = f"{p.floor}階" if p.floor is not None else "—"
        score = str(p.score) if p.score is not None else "—"
        fl = ("[" + ",".join(p.flags) + "]") if p.flags else ""
        print(f"{p.name[:24]:<26}{area:<20}{rent:<11}{park:<6}{floor:<5}{p.gate_result:<10}{score:<5}{p.rank:<6}{p.location_verdict}{fl}")

    # 保存＆差分検知（selftest用の別DB）
    db = ROOT / "data" / "selftest.db"
    if db.exists():
        db.unlink()
    store = Store(db)
    new = store.save_all(all_props, today)
    store.close()

    summary.collected = len(all_props)
    summary.gate_pass = sum(1 for p in all_props if p.gate_result == "pass")
    summary.new_count = len(new)
    summary.parse_notes = all_notes

    out = ROOT / "out"
    h = HtmlReporter(out).emit(all_props, today, summary)
    t = TsvReporter(out).emit(all_props, today, summary)

    print(f"\n収集 {summary.collected} / ゲート通過 {summary.gate_pass} / 新規 {summary.new_count}"
          f" / リンク切れ {summary.dead_links}")
    if all_notes:
        print("パース注意:")
        for n in all_notes:
            print("  ", n)
    print(f"\nHTML: {h}\nTSV : {t}")
    # 差分検知の確認: 2回目は新規0のはず
    store = Store(db); new2 = store.save_all(all_props, today); store.close()
    print(f"差分検知チェック（2回目実行の新規数）: {len(new2)}  ← 0 なら正常")


if __name__ == "__main__":
    main()
