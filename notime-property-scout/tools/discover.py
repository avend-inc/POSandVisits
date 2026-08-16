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
from src import criteria as criteria_mod    # noqa: E402

# 検索条件（面積・家賃・階数・駐車場のしきい値）。main() で Supabase から読み込む。
# レビュー画面の⚙設定で変えた値を収集にも反映する。取得失敗時は既定値。
CRIT = dict(criteria_mod.DEFAULTS)

FEED = ROOT / "feeds" / "bukken.jsonl"
CONTACT = os.environ.get("SCOUT_CONTACT", "sho.nakano@avend.co.jp")
UA = f"notime-property-scout/0.1 (+{CONTACT})"
REQUEST_GAP_SEC = 3.0          # §5.4 1リクエスト/3秒以上
MAX_NEW_PER_RUN = 60           # 1回の総上限（暴走防止）
PER_CITY = 8                   # 1市あたりの上限（1市で埋め尽くさず6市に散らす）
ENRICH_EXISTING = 50           # 既存の家賃/面積が欠けた行を1回あたり最大この数だけ詳細ページで読み直す
PROP_COLS = ["id", "city", "name", "address", "area_tsubo", "area_sqm", "rent_yen",
             "parking", "floor", "station_name", "usage", "source", "detail_url",
             "success_flag", "note"]

# 全サイト共通の「ラベル基準」抽出。各不動産サイトはUIが違っても、賃料/面積/駐車場/
# 階/駅という項目名(ラベル)は共通なので、ラベルの近くの数値を読む。ラベルの言い回しを広く取る。
# 賃料：月額。直後が「坪」のものは坪単価なので除外（負の先読み）。
_RE_RENT = re.compile(r"(?:賃料|家賃|月額賃料|月額|募集賃料)[^\d]{0,10}([\d,]+(?:\.\d+)?)\s*(万円|円)(?![\s/／]*坪)")
# 面積：まず「区画（テナント単位）の面積」を優先し、無ければ建物面積等にフォールバック。
#  ※ 建物面積/延床面積を先に拾うと一棟全体の坪数(例236坪)を掴んで実態とズレるため二段構え。
_RE_AREA_UNIT = re.compile(
    r"(?:使用部分面積|使用面積|専有面積|賃貸面積|店舗面積|貸室面積|区画面積)[^\d]{0,8}"
    r"([\d,]+(?:\.\d+)?)\s*(㎡|m2|m²|平米|坪|帖|畳)")
_RE_AREA_BLDG = re.compile(
    r"(?:建物面積|延床面積|床面積|面積)[^\d]{0,8}"
    r"([\d,]+(?:\.\d+)?)\s*(㎡|m2|m²|平米|坪|帖|畳)")
_RE_PARK = re.compile(r"駐車[場車]?[^\d]{0,8}(\d+)\s*台")
# 階数：「15階建」等の建物規模は拾わない（(?!建)）。物件の所在階のみ。
_RE_FLOOR = re.compile(r"(?:所在階[^\d]{0,4})?(\d{1,2})\s*階(?!建)")
_RE_STATION = re.compile(r"([^\s、。/／]{1,12}駅)\s*(徒歩|車|バス)?\s*(\d+)\s*分")
RENT_MIN = 30000   # 面積抽出時の下限（これ未満は坪単価/管理費の拾い間違いとみなさない）。
# ※ 候補の採否レンジ（面積・家賃・階数・駐車場）は CRIT（＝Supabaseの検索条件）で判定する。
#   「判明していてレンジ外」の物件は candidate から除外し、不明の物件は残す（確認で変わりうるため）。
# 用途/種目（事務所かテナント店舗か）。店舗として出せない事務所・オフィス専用を弾くために使う。
# 値は店舗/事務所/テナント等で始まるものだけ採用（「用途地域→地域」等のノイズを拾わない）。
_RE_USAGE = re.compile(
    r"(?:物件種目|建物種目|種目|使用用途|業種)[：:\s　]{0,4}"
    r"(店舗[^\s　、。/／|｜]{0,6}|事務所[^\s　、。/／|｜]{0,6}|テナント[^\s　、。/／|｜]{0,4}"
    r"|飲食[^\s　、。/／|｜]{0,4}|物販[^\s　、。/／|｜]{0,4}|倉庫|工場|住宅[^\s　、。/／|｜]{0,4})")
_RE_OFFICE = re.compile(r"事務所|オフィス")                       # オフィス系のシグナル
_RE_RETAIL = re.compile(r"店舗|路面|物販|飲食|商店街|アーケード|ショップ|美容|物件種目.*店舗")  # 店舗系のシグナル


def _usage(text: str) -> str | None:
    m = _RE_USAGE.search(text or "")
    return m.group(1).strip() if m else None


def _office_only(text: str) -> bool:
    """事務所・オフィス専用（店舗として出せない）と判断できるか。店舗シグナルがあれば残す。"""
    t = text or ""
    return bool(_RE_OFFICE.search(t)) and not bool(_RE_RETAIL.search(t))


_RE_ROAD = re.compile(r"路面|幹線|国道|バイパス|ロードサイド")
_RE_HASSPEC = re.compile(r"坪|㎡|m2|平米|賃料|家賃|万円|駐車")
# 物件“個別”詳細ページらしいリンク（§9.1：一覧URLは不可。個別のみ拾う）
_RE_DETAIL = re.compile(r"detailPage|/detail[-/]|/bukken[-/]|/property/[^/]*\d|/room/[^/]*\d", re.I)
# 一覧・検索・カテゴリ等（=個別物件ではない）は除外する
_RE_NOTDETAIL = re.compile(r"/result/|/list/|/search|/category|/area_|/grouping/|sp-tenannto", re.I)
# 物件名/建物名ラベル（詳細ページからビル名を拾って「○○市のテナント」の代わりに使う）
_RE_BLDG = re.compile(r"(?:物件名|建物名|ビル名|マンション名|建物名称)[：:\s　]{0,3}([^\s　/／|｜、。]{2,30})")

# 面積等を JS で後読みするため静的取得では面積が取れないサイト。ここに host を足せば対象拡大。
JS_HOSTS = {"house.goo.ne.jp"}
_RENDER_BUDGET = int(os.environ.get("RENDER_BUDGET", "30"))   # 1回のヘッドレス取得の上限（実行時間の暴走防止）
_render_used = 0


def _host(url: str) -> str:
    return urlsplit(url or "").netloc.lower()


def fetch_rendered(url: str) -> str | None:
    """ヘッドレスChromiumでJS描画後の本文テキストを返す。playwright未導入/失敗時はNone（§5.3）。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(user_agent=UA)
            try:
                page.goto(url, wait_until="networkidle", timeout=25000)
            except Exception:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
            txt = page.inner_text("body")
            browser.close()
            return txt
    except Exception as e:
        print(f"  [render-skip] {type(e).__name__}: {url}")
        return None


# ビル名として不適格（＝隣接する項目ラベルや定型語を誤って拾った）と判断する語。
# goo等は spec テーブルで「建物名称」の値が空だと次のラベル(築年月等)を拾ってしまうため広めに弾く。
_RE_BLDG_BAD = re.compile(
    r"築|年月|年数|間取|敷金|礼金|管理費|共益|所在地|交通|構造|階建|入居|保証|条件|"
    r"種別|種類|情報|問合|お問|カテゴリ|検索|一覧|地図|物件|テナント|店舗|事務所|募集|"
    r"賃貸|なし|無し|[()（）]|[都道府県].{0,10}[市区町村]")


def _building_name(text: str) -> str | None:
    """本文から物件名/建物名を拾う（ラベル優先）。ラベルや定型語の誤検出は弾く。無ければ None。"""
    m = _RE_BLDG.search(text or "")
    if not m:
        return None
    name = m.group(1).strip()
    if len(name) < 2 or _RE_BLDG_BAD.search(name):
        return None
    return name


def _is_placeholder_name(rec: dict) -> bool:
    """name が仮名/誤値（住所・「○○のテナント」・ラベル拾い）かどうか（=ビル名に差し替えたい状態）。"""
    nm = rec.get("name") or ""
    if not nm or nm.endswith("のテナント") or nm == (rec.get("address") or ""):
        return True
    if re.match(r"^[^\s]*[都道府県][^\s]*[市区町村]", nm):
        return True
    return bool(_RE_BLDG_BAD.search(nm))    # 既に誤ってラベルを拾っている名も差し替え対象に


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


_MIN_AREA_SQM = 10.0   # これ未満は検索フィルタ枠/サンプル値の誤検出（例: goo詳細の固定「2.0坪＝6.61㎡」）。
                       #  店舗テナントは通常10㎡以上。小さすぎる値は採用せず「不明」にする（誤値より不明が正しい・§9.1.1）。


def _area_from(rx, text):
    """指定ラベル正規表現で「10㎡以上」の最初の面積を返す（極小のフィルタ/サンプル値は捨てる）。"""
    for m in rx.finditer(text or ""):
        val = float(m.group(1).replace(",", ""))
        sqm = val * area_mod.UNIT_TO_SQM.get(m.group(2), 1.0)
        if sqm >= _MIN_AREA_SQM:
            return sqm, sqm / area_mod.TSUBO_TO_SQM
    return None


def _area(text: str):
    """(area_sqm, area_tsubo) を返す。

    優先順位: ①区画(テナント単位)面積 → ②建物面積等 → ③本文パース。
    ①を優先することで、一棟全体の建物面積(例236坪)を掴んで実態とズレるのを防ぐ。
    検索フィルタ枠やサンプルの「2.0坪(=6.61㎡)」等の極小固定値は各段で捨てる。
    """
    return (_area_from(_RE_AREA_UNIT, text) or _area_from(_RE_AREA_BLDG, text)
            or _parse_area_min(text))


def _parse_area_min(text: str):
    try:
        pa = area_mod.parse_area(text)
    except area_mod.ParseError:
        return None
    return pa if (pa and pa[0] >= _MIN_AREA_SQM) else None


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
        if _office_only(text):
            continue                # 事務所・オフィス専用は店舗として出せないので除外
        # 面積(ラベル優先・単位必須)
        _a = _area(text)
        area_sqm, area_tsubo = _a if _a else (None, None)
        rent = _rent(text)
        parking = _int(_RE_PARK, text)
        floor = _int(_RE_FLOOR, text)
        # 面積・家賃・階数・駐車場が「判明していて条件レンジ外」なら候補にしない（不明なら残す）
        if criteria_mod.out_of_range(
                {"area_tsubo": area_tsubo, "rent_yen": rent, "floor": floor, "parking": parking}, CRIT):
            continue
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
        note = desc            # 説明文のみ。無ければ空（カードに定型文を出さない）
        pid = f"{_slug(source)}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"
        if pid in known_ids:
            continue
        success = bool(area_tsubo and area_tsubo >= CRIT["area_min"] and (parking is None or parking >= 2) and roadside)
        rec = {
            "id": pid, "city": city, "name": name[:60], "address": address,
            "area_tsubo": round(area_tsubo, 2) if area_tsubo else None,
            "area_sqm": round(area_sqm, 2) if area_sqm else None,
            "rent_yen": rent, "parking": parking, "floor": floor,
            "station_name": None, "usage": _usage(text),
            "source": source, "detail_url": url, "success_flag": success,
            "note": note,
        }
        out.append({k: rec.get(k) for k in PROP_COLS})
    return out


def enrich(rec: dict) -> dict | None:
    """詳細ページ（個別物件URL）を開いて、家賃・面積・駐車・階を正確に読み直す。

    一覧ページには家賃が無い/坪単価しか無いことが多いので、リンク先まで行って読む。
    3階以上と判明したら除外（None）。取得できなければ一覧由来の値を残す。
    """
    global _render_used
    html = fetch(rec["detail_url"])
    if not html:
        return rec
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)

    # 面積が JS 後読みのサイト（例 goo）は静的取得で面積が取れない。
    # 静的で面積が無く、対象host かつ budget が残っていればヘッドレスで描画後テキストを取り直す。
    if (not _area(text) and _host(rec["detail_url"]) in JS_HOSTS
            and _render_used < _RENDER_BUDGET):
        _render_used += 1
        rtext = fetch_rendered(rec["detail_url"])
        if rtext:
            text = rtext          # 以降の抽出は描画後テキストで行う（家賃/駅等もより正確）

    # 用途スクリーニング：事務所・オフィス専用（店舗として出せない）は除外
    if _office_only(text):
        return None
    usage = _usage(text)
    if usage:
        rec["usage"] = usage

    r = _rent(text)
    if r is not None:
        rec["rent_yen"] = r
    a = _area(text)
    if a:
        rec["area_sqm"] = round(a[0], 2)
        rec["area_tsubo"] = round(a[1], 2)
    pk = _int(_RE_PARK, text)
    if pk is not None:
        rec["parking"] = pk
    fl = _int(_RE_FLOOR, text)
    if fl is not None:
        rec["floor"] = fl
    sm = _RE_STATION.search(text)   # 最寄り駅（例「秋田駅 徒歩11分」）
    if sm:
        rec["station_name"] = f"{sm.group(1)} {sm.group(2) or ''}{sm.group(3)}分".strip()
    # 判明した面積・家賃・階数・駐車場が条件レンジ外なら候補から除外（不明の属性は判定しない）
    if criteria_mod.out_of_range(rec, CRIT):
        return None
    # 物件名が仮名（住所/○○のテナント）のときは詳細ページからビル名を拾って差し替える
    if _is_placeholder_name(rec):
        bn = _building_name(text)
        if bn:
            rec["name"] = bn[:60]
    at, pk2 = rec.get("area_tsubo"), rec.get("parking")
    rec["success_flag"] = bool(at and at >= CRIT["area_min"] and (pk2 is None or pk2 >= 2)
                               and _RE_ROAD.search(text))
    return rec


def _feed_screen_out(r: dict) -> bool:
    """feedに残すべきでない行（未判定の想定）を落とす。

    方針: 「条件に合致しないと判明している」物件は除外し、確認で変わりうるもの（面積・家賃不明など）だけ残す。
    - 面積・家賃・階数・駐車場が判明していて条件レンジ外 → 除外
    - 事務所・オフィス専用 → 除外
    """
    if criteria_mod.out_of_range(r, CRIT):
        return True
    hay = " ".join(str(r.get(k) or "") for k in ("usage", "name", "note"))
    return _office_only(hay)


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
    global CRIT
    CRIT = criteria_mod.fetch()          # レビュー画面の⚙設定（Supabase）を読み込む。失敗時は既定値。
    print(f"検索条件: 面積{CRIT['area_min']}〜{CRIT['area_max']}坪 / 家賃{CRIT['rent_min']}〜{CRIT['rent_max']}円 "
          f"/ 階数≤{CRIT['floor_max']} / 駐車≥{CRIT['parking_min']}")
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

    # 詳細ページを開いて家賃・面積・駐車・階を正確に読み直す（リンク先まで行って読む）
    print(f"\n詳細ページ読み取り {len(found)} 件…")
    enriched = []
    for r in found:
        time.sleep(REQUEST_GAP_SEC)
        e = enrich(r)
        if e is not None:
            enriched.append(e)
    found = enriched
    got_rent = sum(1 for r in found if r.get("rent_yen"))
    print(f"  読み取り後 {len(found)} 件（家賃取得 {got_rent} 件）")

    # 既存feedの「家賃null」行も詳細ページで補完する（1回あたり上限・全体で徐々に埋まる）
    existing = []
    if FEED.exists():
        existing = [json.loads(l) for l in FEED.read_text(encoding="utf-8").splitlines() if l.strip()]
    budget = ENRICH_EXISTING
    kept = []
    for r in existing:
        indiv = bool(re.search(r"detailPage|/detail[-/]|/bukken[-/]", r.get("detail_url") or ""))
        # 家賃 or 面積 が欠けている行は詳細ページで読み直す（一覧の誤家賃も上書き修正される）
        need = indiv and (not r.get("area_tsubo") or not r.get("rent_yen"))
        if budget > 0 and need:
            time.sleep(REQUEST_GAP_SEC)
            e = enrich(r)
            budget -= 1
            if e is None:
                continue            # 3階以上等で除外
            kept.append(e)
        else:
            kept.append(r)
    filled = ENRICH_EXISTING - budget
    print(f"既存の家賃null行を補完: {filled} 件試行")

    all_rows = kept + found            # 既存(補完済) + 新規
    # feed書き出し時の最終スクリーニング（除外条件が後から入っても、次回以降で確実に落とす）。
    #  判定済みはfeedに存在しない（verdictはDB側のみ）ので、ここでの除外は未判定行だけに効く。
    before = len(all_rows)
    all_rows = [r for r in all_rows if not _feed_screen_out(r)]
    if before != len(all_rows):
        print(f"feed最終スクリーニング除外 {before - len(all_rows)} 件（100坪超/事務所専用）")
    with FEED.open("w", encoding="utf-8") as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n合計 新規 {len(found)} 件を追記（feed {len(all_rows)} 件）")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"new_count={len(found)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
