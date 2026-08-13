"""コマンドライン入口。

  python -m src.cli url        --city 大分市           一覧URLを組んで表示（取得しない・確認用）
  python -m src.cli fetch      --city 大分市 [--out samples/oita.html]
                                                       手元で1回だけ一覧を取得して保存（§5.4遵守）
  python -m src.cli parse-file samples/oita.html --city 大分市
                                                       保存HTMLをパースし 坪数/家賃/駐車場/階数 を表示
  python -m src.cli run        [--city X] [--priority 1] [--no-verify]   日次バッチ（§11）
                                                       既定でリンク生存確認あり（掲載終了を除外）
  python -m src.cli linkcheck  <URL...> [--file urls.txt]   個別URLの生存確認（ソフト404検出）
  python -m src.cli selftest                            合成データでパイプライン全体を実証（ネット不要）

実行は手元Mac / 小規模VPS（§10）。この環境（データセンター）からは取得できない。
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from .run import build_properties, load_cities, load_registry, run
from .sources import rengotai

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _find_city(name: str) -> dict:
    for c in load_cities():
        if c["name"] == name:
            return c
    raise SystemExit(f"cities.yaml に {name} がありません")


def _selectors() -> dict:
    reg = load_registry()
    cfg = next((s for s in reg.get("portals_nationwide", [])
                if s.get("parser") == "rengotai"), {})
    return cfg.get("selectors", {})


def cmd_url(args):
    city = _find_city(args.city)
    for u in rengotai.list_urls(city):
        print(u)


def cmd_fetch(args):
    from .http import Fetcher
    city = _find_city(args.city)
    fetcher = Fetcher(contact=args.contact)
    urls = rengotai.list_urls(city)
    html = fetcher.get(urls[0])
    out = Path(args.out) if args.out else Path("samples") / f"{city['pref_roma']}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"saved {len(html):,} bytes -> {out}")
    print("次: python -m src.cli parse-file", out, "--city", city["name"])


def cmd_parse_file(args):
    city = _find_city(args.city)
    html = Path(args.html).read_text(encoding="utf-8")
    raws = rengotai.parse_listing(html, city, _selectors())
    props, notes = build_properties(raws, city, date.today(), area_unit_hint=None)
    print(f"抽出 {len(props)} 件（{city['name']}）\n")
    hdr = f"{'物件名':<20}{'坪数':<22}{'家賃':<12}{'駐車':<7}{'階':<5}{'路面':<5}判定"
    print(hdr)
    print("-" * len(hdr))
    for p in props:
        area = (f"{p.area_tsubo:.1f}坪({p.area_sqm:.1f}㎡)" if p.area_tsubo else "要確認")
        rent = f"¥{p.rent_monthly:,}" if p.rent_monthly else "要相談"
        park = f"{p.parking_slots}台" if p.parking_slots is not None else "—"
        floor = f"{p.floor}階" if p.floor is not None else "—"
        road = "○" if p.roadside_flag else "—"
        name = (p.name or "（名称なし）")[:18]
        print(f"{name:<20}{area:<22}{rent:<12}{park:<7}{floor:<5}{road:<5}{p.rank}")
        print(f"    detail: {p.detail_url}")
    if notes:
        print("\n--- パース注意 ---")
        for n in notes:
            print(" ", n)


def cmd_run(args):
    result = run(cities_filter=[args.city] if args.city else None,
                 priority_only=args.priority, contact=args.contact,
                 check_links=not args.no_verify)
    print(result)


def cmd_linkcheck(args):
    """個別物件URLの生存確認（掲載終了/ソフト404を検出）。"""
    from .http import Fetcher
    from . import linkcheck
    fetcher = Fetcher(contact=args.contact)
    urls = args.urls
    if args.file:
        urls = urls + [ln.strip() for ln in Path(args.file).read_text().splitlines() if ln.strip()]
    for u in urls:
        print(f"{linkcheck.check_url(fetcher, u):<6} {u}")


def cmd_selftest(args):
    from .selftest import main as selftest_main
    selftest_main()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="scout", description="notime-property-scout MVP")
    ap.add_argument("--contact", default="sho.nakano@avend.co.jp", help="UAに載せる連絡先（§5.4）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("url"); p.add_argument("--city", required=True); p.set_defaults(func=cmd_url)
    p = sub.add_parser("fetch"); p.add_argument("--city", required=True); p.add_argument("--out")
    p.set_defaults(func=cmd_fetch)
    p = sub.add_parser("parse-file"); p.add_argument("html"); p.add_argument("--city", required=True)
    p.set_defaults(func=cmd_parse_file)
    p = sub.add_parser("run"); p.add_argument("--city"); p.add_argument("--priority", type=int)
    p.add_argument("--no-verify", action="store_true", help="リンク生存確認をスキップ")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("linkcheck"); p.add_argument("urls", nargs="*")
    p.add_argument("--file", help="URLを1行ずつ書いたファイル")
    p.set_defaults(func=cmd_linkcheck)
    p = sub.add_parser("selftest"); p.set_defaults(func=cmd_selftest)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
