"""RawListing（パーサの素の値）→ Property（§8）への正規化。

単位取り違えの検査（§9.1.1）をこの1箇所に集約する。面積の単位が特定できなければ
その物件の面積は None のままにし「面積単位不明」フラグを立てる（推測で埋めない・§13）。
"""
from __future__ import annotations

import re
from datetime import date

from . import area as area_mod
from .addr import property_key
from .models import Property, RawListing

_RE_NUM = re.compile(r"([\d,]+(?:\.\d+)?)")
_RE_TAX_EXCLUDED = re.compile(r"税別|税抜|\+\s*税|＋税|別途消費税")
_RE_MONTHS = re.compile(r"([\d,]+(?:\.\d+)?)\s*[ヶケか]?月")


def _num(text: str | None) -> float | None:
    if not text:
        return None
    m = _RE_NUM.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def parse_rent(text: str | None) -> tuple[int | None, bool]:
    """家賃テキスト → (月額税込円, 税別を1.1倍換算したか)。

    「26.4万円」→264000、「264,000円」→264000。税別表記は1.1倍して税込に揃える（§9.1 列4）。
    「要相談」等で数値が取れなければ (None, False)。
    """
    if not text:
        return None, False
    m = _RE_NUM.search(text)
    if not m:
        return None, False
    val = float(m.group(1).replace(",", ""))
    if "万" in text:
        val *= 10000
    converted = bool(_RE_TAX_EXCLUDED.search(text))
    if converted:
        val *= 1.1
    return int(round(val)), converted


def parse_parking(text: str | None) -> int | None:
    n = _num(text)
    return int(n) if n is not None else None


def parse_floor(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d{1,2})\s*階", text)
    return int(m.group(1)) if m else None


def parse_months(text: str | None) -> float | None:
    """敷金/礼金/仲介の「Nヶ月」。数値のみ（金額直書き）は None（月数不明）とする。"""
    if not text:
        return None
    m = _RE_MONTHS.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def gmap_url(address: str, city: str, name: str) -> str:
    """§9.1 列8：https://maps.google.com/maps?q={住所文字列}。座標は使わない。"""
    from urllib.parse import quote
    q = address.strip() or f"{city}{name}".strip()
    return f"https://maps.google.com/maps?q={quote(q)}"


def normalize(
    raw: RawListing,
    today: date,
    area_unit_hint: str | None = None,
) -> tuple[Property, list[str]]:
    """RawListing を Property に正規化する。(property, notes) を返す。

    notes はパース時の注意（面積単位不明・税込換算など）。run側でサマリに集計する。
    area_unit_hint を渡すと §9.1.1 の単位不一致検査を行う（MVPは未verifiedのため既定 None）。
    """
    notes: list[str] = []

    # --- 面積（§9.1.1・最重要。単位不明なら埋めない） ---
    area_sqm = area_tsubo = None
    area_unit_raw = None
    if raw.area_text:
        try:
            area_sqm, area_tsubo = area_mod.parse_area(raw.area_text, hint=area_unit_hint)
            m = re.search(r"(㎡|m2|m²|平米|坪|帖|畳)", raw.area_text)
            area_unit_raw = m.group(1) if m else None
        except area_mod.UnitMismatch as e:
            notes.append(f"面積単位不一致→broken候補: {e}")
        except area_mod.ParseError:
            notes.append(f"面積単位不明: {raw.area_text!r}")

    # --- 家賃（税込へ） ---
    rent_monthly, tax_converted = parse_rent(raw.rent_text)
    if raw.rent_text and rent_monthly is None:
        notes.append(f"家賃要相談/未記載: {raw.rent_text!r}")

    parking = parse_parking(raw.parking_text)
    floor = parse_floor(raw.floor_text)

    deposit_m = parse_months(raw.deposit_text)
    key_m = parse_months(raw.key_money_text)
    broker_m = parse_months(raw.brokerage_text)

    # --- 取得費（§2 G6：初月家賃 + 敷金 + 礼金 + 仲介） ---
    acquisition = None
    if rent_monthly is not None and None not in (deposit_m, key_m, broker_m):
        acquisition = int(round(
            rent_monthly * (1 + deposit_m + key_m + broker_m)
        ))

    pid = property_key(raw.address, city=raw.city)

    p = Property(
        id=pid,
        source_domain=raw.source_domain,
        detail_url=raw.detail_url,
        first_seen=today,
        last_seen=today,
        prefecture=raw.prefecture,
        city=raw.city,
        address=raw.address,
        name=raw.name or "",
        area_sqm=round(area_sqm, 2) if area_sqm is not None else None,
        area_tsubo=round(area_tsubo, 2) if area_tsubo is not None else None,
        area_unit_raw=area_unit_raw,
        floor=floor,
        parking_slots=parking,
        rent_monthly=rent_monthly,
        rent_is_tax_included=True,
        deposit_months=deposit_m,
        key_money_months=key_m,
        brokerage_months=broker_m,
        acquisition_cost=acquisition,
        roadside_flag=raw.roadside_flag,
        listing_agent=raw.listing_agent,
        raw_rent_text=raw.rent_text,
        gmap_url=gmap_url(raw.address, raw.city, raw.name or ""),
    )
    if tax_converted:
        notes.append("家賃を税込換算(×1.1)")
    return p, notes
