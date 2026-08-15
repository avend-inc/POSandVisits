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
MAX_NEW_PER_RUN = 60           # 1回の総上限（暴走防止）
PER_CITY = 8                   # 1市あたりの上限（1市で埋め尽くさず6市に散らす）
PROP_COLS = ["id", "city", "name", "address", "area_tsubo", "area_sqm", "rent_yen",
             "parking", "floor", "source", "detail_url", "success_flag", "note"]

# カード本文からのスペック抽出（連合隊パーサと同じ二段構えの正規表現側）
# 賃料：月額。直後が「坪」のものは坪単価なので除外（負の先読み）。
_RE_RENT = re.compile(r"(?:賃料|家賃)[^\d]{0,8}([\d,]+(?:\.\d+)?)\s*(万円|円)(?![\s/／]*坪)")
_RE_PARK = re.compile(r"駐車[場車]?[^\d]{0,6}(\d+)\s*台")
# 階数：「15階建」等の建物階数は拾わない（(?!建)）。物件の所在階のみ。
_RE_FLOOR = re.compile(r"(\d{1,2})\s*階(?!建)")
RENT_MIN = 30000   # これ未満は坪単価/管理費の拾い間違いとみなし採用しない
_RE_ROAD = re.compile(r"路面|幹線|国道|バイパス|ロードサイド")
_RE_HASSPEC = re.compile(r"坪|㎡|m2|平米|賃料|家賃|万円|駐車")
# 物件“個別”詳細ページらしいリンク（§9.1：一覧URLは不可。個別のみ拾う）
_RE_DETAIL = re.compile(r"detailPage|/detail[-/]|/bukken[-/]|/property/[^/]*\d|/room/[^/]*\d", re.I)
# 一覧・検索・カテゴリ等（=個別物件ではない）は除外する
_RE_NOTDETAIL = re.compile(r"/result/|/list/|/search|/category|/area_|/grouping/|sp-tenannto", re.I)


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
    """月額賃料（税込目安）。坪単価・管理費・小額は採用しない。"""
    for m in _RE_RENT.finditer(text):
        val = float(m.group(1).replace(",", ""))
        if "万" in m.group(2):
            val *= 10000
        if val >= RENT_MIN:      # 坪単価/管理費らしい小額は捨てる
            return int(round(val))
    return None


def _int(rx, text):
    m = rx.search(text)
    return int(m.group(1)) if m else None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "src"


_RE_ADDR = re.compile(r"[^\s、。,，]{0,4}[都道府県][^\s、。,，]{1,8}[市区町村][^\s、。,，]{0,15}")


def _address(text: str) -> str:
    m = _RE_ADDR.search(text or "")
    return m.group(0) if m else ""


def extract(html: str, source: str, city: str, base_url: str,
            known_ids: set, known_urls: set) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _RE_DETAIL.search(href) or _RE_NOTDETAIL.search(href):
            continue
        url = urljoin(base_url, href.split("#")[0])
        if url == base_url or url in seen or url in known_urls:
            continue      # 一覧URL自身や既知URLは弾く（§9.1 一覧URLは不可）
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
        if floor is not None and floor >= 3:
            continue                # 1階・2階のみ（§2 G2。3階以上は除外）
        roadside = bool(_RE_ROAD.search(text))
        anchor = a.get_text(" ", strip=True)
        # 月極駐車場等はテナントでないので除外
        if re.search(r"駐車場|月極|パーキング|駐輪|コインパーク", anchor):
            continue
        # 住所（都道府県…市区町村…）をアンカー/本文から拾う
        address = _address(anchor) or _address(text)
        # アンカーが短く句読点なし＝物件名/住所。長文＝説明文として分離する。
        if len(anchor) <= 22 and not re.search(r"[。、！!？?]", anchor):
            name, desc = anchor, ""
        else:
            name, desc = (address or f"{city}のテナント"), anchor
        note = desc or "自動収集(GitHub Actions)・スペックはページ記載分のみ"
        pid = f"{_slug(source)}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"
        if pid in known_ids:
            continue
        success = bool(area_tsubo and area_tsubo >= 25 and (parking is None or parking >= 2) and roadside)
        rec = {
            "id": pid, "city": city, "name": name[:60], "address": address,
            "area_tsubo": round(area_tsubo, 2) if area_tsubo else None,
            "area_sqm": round(area_sqm, 2) if area_sqm else None,
            "rent_yen": rent, "parking": parking, "floor": floor,
            "source": source, "detail_url": url, "success_flag": success,
            "note": note,
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
    from collections import defaultdict
    known_ids, known_urls = load_known()
    found: list[dict] = []
    per_city = defaultdict(int)         # 市ごとの採用数（1市で埋め尽くさない）
    fetched = 0
    for city, name, url in sources():
        if len(found) >= MAX_NEW_PER_RUN:
            break
        if per_city[city] >= PER_CITY:
            continue                    # この市は充足。取得もしない（無駄打ち回避）
        if fetched:
            time.sleep(REQUEST_GAP_SEC)
        fetched += 1
        print(f"[{city}] {name}: {url}")
        html = fetch(url)
        if not html:
            continue
        try:
            recs = extract(html, name, city, url, known_ids, known_urls)
        except Exception as e:
            print(f"  [parse-fail] {type(e).__name__}: {e}")
            continue
        added = 0
        for r in recs:
            if per_city[city] >= PER_CITY:
                break
            if r["id"] in known_ids or r["detail_url"] in known_urls:
                continue
            known_ids.add(r["id"]); known_urls.add(r["detail_url"])
            found.append(r); per_city[city] += 1; added += 1
        print(f"  抽出 新規 {added} 件（市計 {per_city[city]}）")

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
