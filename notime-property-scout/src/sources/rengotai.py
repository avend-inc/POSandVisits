"""不動産連合隊（fudosanlist.cbiz.ne.jp）— MVPの唯一のソース（§5.1.1 / §11）。

最優先の理由（§5.1.1）: ゲート条件がURLパラメータでほぼ表現できるため、取得段階で
ノイズを落とせる。物件カードに坪単価/坪数/階数/駐車場が構造化済み。

注意（§5.1.1）:
  - 物件詳細は fudosan.cbiz.ne.jp/detailPage/rent/{supplier_id}/{property_id}/ という
    別ドメイン。個別物件URLはこちらを使う（一覧URLは不可）。
  - meta-robots: noarchive のためHTMLキャッシュは保存せず、抽出した構造化データのみ保持する。

⚠ URLパラメータの実キー名と、カードのHTML構造（CSSセレクタ）は、実ページを1回取得して
  確定させること（§5.2「人のレビュー（必須）」）。本ファイルの URL 構築は §5.1.1 に列挙された
  フィルタ意図をパラメータ化した**暫定実装**であり、パーサはセレクタが外れても
  カード本文の正規表現でスペックを拾える二段構えにしてある。verified にする前に実HTMLで確認。
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import RawListing

LIST_BASE = "https://fudosanlist.cbiz.ne.jp/list/rent/"  # 実パス（WebSearchで実URL構造を確認）
DETAIL_HOST = "fudosan.cbiz.ne.jp"                        # 個別物件ドメイン（§5.1.1）

DOMAIN = "fudosanlist.cbiz.ne.jp"

# 実在が確認できたパラメータ（例: /list/rent/?prop=2&area=oita&a2=44201）:
#   prop = 2      テナント（貸店舗・事務所・倉庫）
#   area = 県名ローマ字（fukushima / akita / shizuoka / oita / miyazaki / nagasaki）
#   a2   = JIS5桁の市区町村コード（cities.yaml の jis）
CONFIRMED_PARAMS = {"prop": "2"}

# 面積25坪以上/駐車2台/1-2階/路面/幹線/坪単価上限/広い順 のURL絞り込みキー名は
# 実フォーム未確認（この環境から取得できずソースを読めていない）。誤ったキーを付けると
# 逆に空振り/エラーになりうるため、URLでは絞らず §2 ゲートで確実に絞る方針にする。
# 実ページのフォームを確認できたら、ここに面積下限・駐車・階・路面・並び順を追加する。


def list_urls(city: dict, max_pages: int = 1) -> list[str]:
    """対象市のテナント一覧URLを組む。実URL構造 /list/rent/?prop=2&area={県ローマ字}&a2={JIS}。

    MVPは1市1ページから。ページングのキー名は実ページ確認後に有効化する。
    面積/駐車/階/路面のURL絞り込みは未確認のため付けない（§2ゲートで絞る）。
    """
    params = dict(CONFIRMED_PARAMS)
    params["area"] = city["pref_roma"]
    params["a2"] = city["jis"]
    urls = []
    for page in range(1, max_pages + 1):
        p = dict(params)
        if page > 1:
            p["page"] = str(page)   # ページングのキー名は要確認（PROVISIONAL）
        urls.append(f"{LIST_BASE}?{urlencode(p)}")
    return urls


# --- カード本文からのスペック抽出（セレクタが外れても効く正規表現・§9.1.1準拠） ---
_RE_AREA = re.compile(r"[\d,]+(?:\.\d+)?\s*(?:㎡|m2|m²|平米|坪|帖|畳)")
_RE_RENT = re.compile(r"(?:賃料|家賃)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(万円|円)")
_RE_PARK = re.compile(r"駐車[場車]?[^\d]{0,6}(\d+)\s*台")
_RE_FLOOR = re.compile(r"(?:^|[^\d])(\d{1,2})\s*階")
_RE_ROADSIDE = re.compile(r"路面|幹線道路沿い|ロードサイド|国道沿い|バイパス沿い")


def _txt(el) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


def _first(card, selector: str | None):
    if not selector:
        return None
    try:
        return card.select_one(selector)
    except Exception:
        return None


def _extract_detail(card):
    """個別物件URL（fudosan.cbiz.ne.jp/detailPage/...）と、そのアンカー文字列を拾う。

    一覧URLは不可（§9.1）。アンカー文字列は物件名のフォールバックに使う。
    戻り値: (url, anchor_text) または (None, "")。
    """
    for a in card.find_all("a", href=True):
        href = a["href"]
        if "detailPage" in href or DETAIL_HOST in href:
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = f"https://{DETAIL_HOST}{href}"
            return href, _txt(a)
    return None, ""


def parse_listing(html: str, city: dict, selectors: dict | None = None) -> list[RawListing]:
    """一覧ページHTMLから RawListing を抽出（§5.1.1）。

    二段構え:
      1) registry.yaml のセレクタでカード要素と各フィールドを取る（verified時の正道）
      2) セレクタが外れたら、カード本文の正規表現でスペックを拾う（破損検知の保険）
    detail_url が取れないカードは捨てる（一覧URLを個別URLとして使わないため・§9.1）。
    """
    selectors = selectors or {}
    soup = BeautifulSoup(html, "lxml")

    item_sel = selectors.get("item")
    cards = []
    if item_sel:
        try:
            cards = soup.select(item_sel)
        except Exception:
            cards = []
    if not cards:
        # フォールバック: detailPage へのリンクを持つ祖先要素をカードとみなす。
        seen = set()
        for a in soup.find_all("a", href=True):
            if "detailPage" in a["href"] or DETAIL_HOST in a["href"]:
                card = a.find_parent(["li", "article", "div", "tr"]) or a.parent
                key = id(card)
                if key not in seen:
                    seen.add(key)
                    cards.append(card)

    results: list[RawListing] = []
    for card in cards:
        detail_url, anchor_text = _extract_detail(card)
        if not detail_url:
            continue
        body = _txt(card)

        name_el = _first(card, selectors.get("name"))
        name = _txt(name_el) if name_el else ""
        if not name:
            # 物件名の主フォールバックは詳細リンクのアンカー文字列（§9.1 列2）。
            name = anchor_text

        area_el = _first(card, selectors.get("area"))
        area_text = _txt(area_el) if area_el else None
        if not area_text:
            m = _RE_AREA.search(body)
            area_text = m.group(0) if m else None

        rent_el = _first(card, selectors.get("rent"))
        rent_text = _txt(rent_el) if rent_el else None
        if not rent_text:
            m = _RE_RENT.search(body)
            rent_text = m.group(0) if m else None

        park_el = _first(card, selectors.get("parking"))
        parking_text = _txt(park_el) if park_el else None
        if not parking_text:
            m = _RE_PARK.search(body)
            parking_text = m.group(0) if m else None

        floor_el = _first(card, selectors.get("floor"))
        floor_text = _txt(floor_el) if floor_el else None
        if not floor_text:
            m = _RE_FLOOR.search(body)
            floor_text = m.group(0) if m else None

        # 掲載元の路面店/幹線道路沿いフラグ（MVPのG4暫定判定・§2）。
        # 見つからなければ None（＝不明。False にしない。§2.2の取りこぼし防止思想）。
        roadside = True if _RE_ROADSIDE.search(body) else None

        results.append(RawListing(
            source_domain=DOMAIN,
            detail_url=detail_url,
            prefecture=city["pref"],
            city=city["name"],
            name=name,
            address="",   # 住所は詳細ページ側にあることが多い。MVPは一覧から取れた分のみ。
            area_text=area_text,
            rent_text=rent_text,
            parking_text=parking_text,
            floor_text=floor_text,
            roadside_flag=roadside,
        ))
    return results
