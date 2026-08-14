"""毎朝の物件discovery（GitHub Actions 実行前提）。

この環境(Claudeセッション)は不動産サイトへ出られないが、GitHub Actions は
インターネットに出られる。よって実サイトを取得し、物件を抽出して feeds/bukken.jsonl
に新規だけ追記する。追記後は sync_bukken.py が Supabase へ反映し、新規があれば Slack通知。

方針:
- 取得先URLは src.linksheet の6市×全ソース(ポータル＋地場)を再利用（semi=bot遮断は除外）。
- ソース単位で例外を握りつぶす(§5.3)。1サイト落ちても全体を止めない。
- 面積は src.area.parse_area で厳格に(単位不明はNone。推測で埋めない・§9.1.1)。
- 数値はページに明記された分だけ。無ければ null。捏造しない。
- 既存 feed の id / detail_url と重複するものは追加しない(新規のみ)。
- ⚠ カード抽出はヒューリスティック(汎用)。CIログの結果を見てサイト別に精緻化していく。
  データセンターIPが遮断されるサイト(§10)は取得失敗としてスキップされる。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import area as area_mod            # noqa: E402
from src import linksheet                   # noqa: E402

FEED = ROOT / "feeds" / "bukken.jsonl"
CONTACT = os.environ.get("SCOUT_CONTACT", "sho.nakano@avend.co.jp")
UA = f"notime-property-scout/0.1 (+{CONTACT})"
REQUEST_GAP_SEC = 3.0          # §5.4 1リクエスト/3秒以上
MAX_NEW_PER_RUN = 40           # 暴走防止
PROP_COLS = ["id", "city", "name", "area_tsubo", "area_sqm", "rent_yen",
             "parking", "floor", "source", "detail_url", "success_flag", "note"]

# カード本文からのスペック抽出（連合隊パーサと同じ二段構えの正規表現側）
_RE_RENT = re.compile(r"(?:賃料|家賃)[^\d]{0,8}([\d,]+(?:\.\d+)?)\s*(万円|円)")
_RE_RENT_ANY = re.compile(r"([\d,]+(?:\.\d+)?)\s*(万円)")
_RE_PARK = re.compile(r"駐車[場車]?[^\d]{0,6}(\d+)\s*台")
_RE_FLOOR = re.compile(r"(?:^|[^\d])(\d{1,2})\s*階")
_RE_ROAD = re.compile(r"路面|幹線|国道|バイパス|ロードサイド")
_RE_HASSPEC = re.compile(r"坪|㎡|m2|平米|賃料|家賃|万円|駐車")
# 物件詳細ページらしいリンク
_RE_DETAIL = re.compile(r"detailPage|/detail[-/]|/bukken/|/property/|/room/|/rent[_/].*\d", re.I)


def load_known():
    ids, urls = set(), set()
    if FEED.exists():
        for line in FEED.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            ids.add(o.get("id"))
            if o.get("detail_url"):
                urls.add(o["detail_url"])
    return ids, urls


def fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        if r.status_code >= 400:
            print(f"  [skip] HTTP {r.status_code}: {url}")
            return None
        r.encoding = r.apparent_encoding or r.encoding
        return r.text
    except Exception as e:
        print(f"  [skip] {type(e).__name__}: {url} ({e})")
        return None


def _rent(text: str) -> int | None:
    m = _RE_RENT.search(text) or _RE_RENT_ANY.search(text)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    if "万" in m.group(0):
        val *= 10000
    return int(round(val))


def _int(rx, text):
    m = rx.search(text)
    return int(m.group(1)) if m else None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "src"


def extract(html: str, source: str, city: str, base_url: str,
            known_ids: set, known_urls: set) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _RE_DETAIL.search(href):
            continue
        url = urljoin(base_url, href.split("#")[0])
        if url in seen or url in known_urls:
            continue
        card = a.find_parent(["li", "article", "div", "tr", "section"]) or a.parent
        text = card.get_text(" ", strip=True) if card else ""
        if not _RE_HASSPEC.search(text):
            continue                      # 物件っぽくない(ナビ等)は捨てる
        seen.add(url)
        # 面積(単位必須)
        area_sqm = area_tsubo = None
        try:
            area_sqm, area_tsubo = area_mod.parse_area(text)
        except area_mod.ParseError:
            pass
        rent = _rent(text)
        parking = _int(_RE_PARK, text)
        floor = _int(_RE_FLOOR, text)
        roadside = bool(_RE_ROAD.search(text))
        name = a.get_text(" ", strip=True)[:60] or (text[:40] if text else "（名称なし）")
        pid = f"{_slug(source)}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"
        if pid in known_ids:
            continue
        success = bool(area_tsubo and area_tsubo >= 25 and (parking is None or parking >= 2) and roadside)
        rec = {
            "id": pid, "city": city, "name": name,
            "area_tsubo": round(area_tsubo, 2) if area_tsubo else None,
            "area_sqm": round(area_sqm, 2) if area_sqm else None,
            "rent_yen": rent, "parking": parking, "floor": floor,
            "source": source, "detail_url": url, "success_flag": success,
            "note": "自動収集(GitHub Actions)。スペックはページ記載分のみ・要確認",
        }
        out.append({k: rec[k] for k in PROP_COLS})
    return out


def sources() -> list[tuple[str, str, str]]:
    """(city, source_name, list_url)。semi(bot遮断)は除外。"""
    lst = []
    for city, m in linksheet.CITY_META.items():
        for name, url, kind in linksheet.portal_links(m) + linksheet.EXTRA_LINKS.get(city, []):
            if kind == "semi":
                continue
            lst.append((city, name, url))
    return lst


def main() -> int:
    known_ids, known_urls = load_known()
    found: list[dict] = []
    for i, (city, name, url) in enumerate(sources()):
        if len(found) >= MAX_NEW_PER_RUN:
            break
        if i:
            time.sleep(REQUEST_GAP_SEC)
        print(f"[{city}] {name}: {url}")
        html = fetch(url)
        if not html:
            continue
        try:
            recs = extract(html, name, city, url, known_ids, known_urls)
        except Exception as e:
            print(f"  [parse-fail] {type(e).__name__}: {e}")
            continue
        for r in recs:
            if r["id"] in known_ids or r["detail_url"] in known_urls:
                continue
            known_ids.add(r["id"]); known_urls.add(r["detail_url"])
            found.append(r)
        print(f"  抽出 新規 {len(recs)} 件")

    if found:
        with FEED.open("a", encoding="utf-8") as f:
            for r in found[:MAX_NEW_PER_RUN]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n合計 新規 {len(found)} 件を feed に追記")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"new_count={len(found)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
