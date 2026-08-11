"""日次バッチ本体（§11）。

流れ: 一覧取得 → パース → 正規化 → ゲート(§2) → スコア(§3) → 立地判定(§3.3)
      → SQLite保存 & 差分検知(§6) → HTML/TSV出力(§9)。

§5.3 の絶対条件: ソース単位で例外を握りつぶす。1サイトが落ちても全体を止めない。
AtHome(§5.3)は本MVPには含めない（非同期の別プロセス。第3段階）。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import yaml

from . import scoring
from .db import DEFAULT_DB, Store
from .gates import evaluate_gates
from .location import evaluate_location
from .models import Property, RawListing
from .normalize import normalize
from .reporters import HtmlReporter, TsvReporter
from .reporters.base import RunSummary
from .sources import rengotai

log = logging.getLogger("scout")
ROOT = Path(__file__).resolve().parent.parent


def load_cities(path: Path | str = ROOT / "cities.yaml") -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return data["cities"]


def load_registry(path: Path | str = ROOT / "registry.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def process_property(p: Property, city_warn: bool) -> Property:
    """1物件に ゲート→フラグ→スコア→立地→ランク を適用する（純関数・ネット不要）。"""
    outcome = evaluate_gates(p)
    p.gate_result = outcome.result
    p.gate_failed_on = outcome.failed_on + outcome.incomplete_on
    p.flags = list(outcome.flags)
    if city_warn:
        p.flags.append("商圏未確定")  # §6.0.1

    # 想定月商レンジ（§3.1）— 面積があれば常に併記
    if p.area_tsubo is not None:
        p.est_sales_low, p.est_sales_mid, p.est_sales_high = scoring.estimated_sales(p)
        if scoring.rent_overload(p):
            p.flags.append("賃料過大")  # §3.1 回収チェック

    # スコア（§3）— 除外以外で算出（並び順にのみ使用・§9.1）
    if p.gate_result != "fail":
        p.score = scoring.score(p)

    # AI評価＝立地類似（§3.3）— MVPは全件「判定不可」
    verdict = evaluate_location(p, city_warn=city_warn)
    p.location_verdict = verdict.verdict
    p.location_pattern = verdict.pattern
    p.location_reason = verdict.reason

    p.rank = scoring.rank(p)
    return p


def build_properties(raws: list[RawListing], city: dict, today: date,
                     area_unit_hint: str | None = None) -> tuple[list[Property], list[str]]:
    """RawListing群 → 処理済み Property群（差分検知前）。notesも集約して返す。"""
    props: list[Property] = []
    notes: list[str] = []
    warn = bool(city.get("warn"))
    for raw in raws:
        p, ns = normalize(raw, today, area_unit_hint=area_unit_hint)
        p.station_name = city.get("main_station")
        process_property(p, warn)
        props.append(p)
        notes.extend(f"[{city['name']}] {n}" for n in ns)
    return props, notes


def dedupe(props: list[Property]) -> list[Property]:
    """§8.1 同一キーは最も条件の良い掲載（家賃が安い/情報が多い）を採用。"""
    best: dict[str, Property] = {}
    for p in props:
        cur = best.get(p.id)
        if cur is None:
            best[p.id] = p
            continue
        # 情報量（Noneでない主要スペック数）→ 家賃の安さ で優劣
        def info(x: Property) -> int:
            return sum(v is not None for v in
                       (x.area_tsubo, x.rent_monthly, x.parking_slots, x.floor))
        if (info(p), -(p.rent_monthly or 10**9)) > (info(cur), -(cur.rent_monthly or 10**9)):
            best[p.id] = p
    return list(best.values())


def verify_links(fetcher, props: list[Property], summary: RunSummary) -> None:
    """各物件の個別URLの生存を確認し、死んでいたら除外する（掲載終了対策）。

    連合隊はソフト404（200のまま本文だけ不存在）なので本文判定する（linkcheck）。
    - dead  : rank=除外・flags に「リンク切れ」
    - error : flags に「リンク未確認」（判断保留＝そのまま残す）
    レポートに載る候補（除外以外）だけを確認して無駄打ちを減らす（§5.4 間隔遵守）。
    """
    from . import linkcheck
    summary.link_check_ran = True
    for p in props:
        if p.rank == "除外":
            continue
        verdict = linkcheck.check_url(fetcher, p.detail_url)
        if verdict == linkcheck.DEAD:
            p.rank = "除外"
            if "リンク切れ" not in p.flags:
                p.flags.append("リンク切れ")
            if "リンク切れ" not in p.gate_failed_on:
                p.gate_failed_on.append("リンク切れ")
            summary.dead_links += 1
        elif verdict == linkcheck.ERROR:
            if "リンク未確認" not in p.flags:
                p.flags.append("リンク未確認")
            summary.unverified_links += 1


def run(cities_filter: list[str] | None = None,
        db_path: Path | str = DEFAULT_DB,
        contact: str = "sho.nakano@avend.co.jp",
        priority_only: int | None = None,
        check_links: bool = True) -> dict:
    """全市を巡回して日次レポートを出す。戻り値はサマリ辞書。

    §5.3: ソース単位で try/except。1市/1サイトの失敗で全体を止めない。
    """
    from .http import Fetcher  # 遅延import（ネット不要のテストでhttpを触らせない）

    today = date.today()
    cities = load_cities()
    registry = load_registry()
    src_cfg = next((s for s in registry["sources"] if s.get("parser") == "rengotai"), {})
    selectors = src_cfg.get("selectors", {})
    hint = selectors.get("area_unit_hint")

    if cities_filter:
        cities = [c for c in cities if c["name"] in cities_filter]
    if priority_only is not None:
        cities = [c for c in cities if c.get("priority") == priority_only]

    fetcher = Fetcher(contact=contact)
    store = Store(db_path)
    summary = RunSummary()
    all_props: list[Property] = []

    for city in cities:
        try:
            urls = rengotai.list_urls(city)
            city_raws: list[RawListing] = []
            for url in urls:
                html = fetcher.get(url)          # noarchive: 保存しない（§5.1.1）
                city_raws.extend(rengotai.parse_listing(html, city, selectors))
            props, notes = build_properties(city_raws, city, today,
                                            area_unit_hint=None)  # MVPはhint未強制（未verified）
            summary.parse_notes.extend(notes)
            all_props.extend(props)
        except Exception as e:
            # §5.3: 絶対に raise しない。broken を記録して次へ。
            log.error("source failed city=%s: %s", city["name"], e)
            summary.broken_sources.append(f"{rengotai.DOMAIN}({city['name']})")
            continue

    all_props = dedupe(all_props)

    # リンク生存確認（掲載終了で消えた詳細ページを除外）。§5.4の間隔は fetcher が守る。
    if check_links:
        verify_links(fetcher, all_props, summary)

    new_props = store.save_all(all_props, today)
    store.close()

    summary.collected = len(all_props)
    summary.gate_pass = sum(1 for p in all_props if p.gate_result == "pass")
    summary.new_count = len(new_props)

    for reporter in (HtmlReporter(), TsvReporter()):
        reporter.emit(all_props, today, summary)

    return {
        "collected": summary.collected,
        "gate_pass": summary.gate_pass,
        "new": summary.new_count,
        "dead_links": summary.dead_links,
        "unverified_links": summary.unverified_links,
        "broken": summary.broken_sources,
    }
