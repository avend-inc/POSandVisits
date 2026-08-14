"""出力9列の共通整形（§9.1）。HTML・TSV・スプレッドシートで同一の値を使う。"""
from __future__ import annotations

import re

from ..area import format_area
from ..models import Property

COLUMN_HEADERS = [
    "見つけた日", "物件名", "坪数", "家賃", "駐車場台数",
    "主要駅からの距離", "物件のリンク", "Googlemapのリンク", "AI評価",
]

_RE_TAX_EXCLUDED = re.compile(r"税別|税抜|\+\s*税|＋税|別途消費税")


def col_found_date(p: Property) -> str:
    return p.first_seen.isoformat()


def col_name(p: Property) -> str:
    # 空の場合は住所を代用し「（物件名なし）」を付す（§9.1 列2）
    if p.name:
        return p.name
    base = p.address or f"{p.city}"
    return f"{base}（物件名なし）"


def col_area(p: Property) -> str:
    # §9.1.1：坪を主・㎡を括弧内。両方無ければ要確認。
    if p.area_tsubo is None or p.area_sqm is None:
        return "要確認"
    return format_area(p.area_tsubo, p.area_sqm, p.area_unit_raw)


def col_rent(p: Property) -> str:
    if p.rent_monthly is None:
        return "要相談"
    s = f"¥{p.rent_monthly:,}"
    if p.raw_rent_text and _RE_TAX_EXCLUDED.search(p.raw_rent_text):
        s += "（税込換算）"
    return s


def col_parking(p: Property) -> str:
    # 未記載は「要確認」、ゼロは「0台（近隣要確認）」（§9.1 列5）
    if p.parking_slots is None:
        return "要確認"
    if p.parking_slots == 0:
        return "0台（近隣要確認）"
    return f"{p.parking_slots}台"


def col_station(p: Property) -> str:
    # 車距離（徒歩ではない・§9.1 列6）。MVPは§7未実装のため未取得→要確認。
    if p.station_distance_km is None:
        name = p.station_name or ""
        return f"{name} 要確認".strip()
    drive = f"（車{p.station_drive_min}分）" if p.station_drive_min is not None else ""
    return f"{p.station_name or ''} {p.station_distance_km:.1f}km{drive}".strip()


def col_detail_link(p: Property) -> str:
    # 個別物件ページURL（一覧URLは不可・§9.1 列7）
    return p.detail_url


def col_gmap_link(p: Property) -> str:
    # 住所文字列で構成（座標は使わない・§9.1 列8）
    return p.gmap_url


def col_ai(p: Property) -> str:
    # §3.3：成功店類似／失敗店類似／判定不可＋パターン名。立地のみ。
    v = p.location_verdict
    if p.location_pattern:
        detail = f"{p.location_pattern}：{p.location_reason}" if p.location_reason else p.location_pattern
        return f"{v}（{detail}）"
    if p.location_reason:
        return f"{v}（{p.location_reason}）"
    return v


def row9(p: Property) -> list[str]:
    """§9.1 の9列を順に返す。"""
    return [
        col_found_date(p),
        col_name(p),
        col_area(p),
        col_rent(p),
        col_parking(p),
        col_station(p),
        col_detail_link(p),
        col_gmap_link(p),
        col_ai(p),
    ]
