"""データモデル（§8）。

収集対象は§8の全項目（坪数・家賃・敷金・礼金・仲介・取得費・階数・坪単価・駐車場…）。
MVPで埋まらない補強項目（road_class / chain_restaurants_500m 等・§7）は None のまま持つ。
AI評価(§3.3)の入力に面積・家賃・坪単価は混ぜない（§13）が、収集・保存・ゲート・
スコア・想定月商には全項目を使う（§3.3の注記）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Property:
    # --- 識別 ---
    id: str                         # §8.1 normalize(address)[+latlng] のハッシュ
    source_domain: str
    detail_url: str                 # 必ず個別物件URL。一覧ページURLは不可（§5.1.1/§9.1）
    first_seen: date                # §9.1 列1：こちらが最初に検出した日
    last_seen: date

    # --- 基本 ---
    prefecture: str
    city: str
    address: str
    name: str
    lat: float | None = None        # MVPは§7未実装のため None
    lng: float | None = None

    # --- スペック ---
    area_sqm: float | None = None       # §9.1.1 必ず両方保持
    area_tsubo: float | None = None
    area_unit_raw: str | None = None    # 掲載元の原単位。推測で埋めない（§9.1.1）
    floor: int | None = None
    parking_slots: int | None = None
    rent_monthly: int | None = None     # 月額・税込に正規化した値
    rent_is_tax_included: bool = True   # 税別掲載を1.1倍したら False→True に補正済みか
    deposit_months: float | None = None
    key_money_months: float | None = None
    brokerage_months: float | None = None
    acquisition_cost: int | None = None  # 初月家賃 + 敷金 + 礼金 + 仲介（§8/§2 G6）

    # --- 補強（§7・MVPは全て None） ---
    road_class: str | None = None
    distance_to_trunk_m: float | None = None
    roadside_flag: bool | None = None    # 掲載元の「路面店」「幹線道路沿い」（MVPのG4暫定判定に使用）
    chain_restaurants_500m: int | None = None
    large_sc_500m: bool | None = None
    secondstreet_within_2km: bool | None = None
    in_shopping_arcade: bool | None = None
    station_name: str | None = None      # cities.yaml の main_station
    station_distance_km: float | None = None  # 道路距離（直線ではない）
    station_drive_min: int | None = None

    # --- 判定 ---
    gate_result: str = "incomplete"      # pass | fail | incomplete
    gate_failed_on: list[str] = field(default_factory=list)
    score: int | None = None             # 並び順にのみ使用。出力列にはしない（§9.1）
    rank: str = "要確認"                 # A | B | C | 要確認 | 除外
    location_verdict: str = "判定不可"   # 成功店類似 | 失敗店類似 | 判定不可（§3.3）
    location_pattern: str | None = None  # 徳島沖浜型 / 仙台吉成型 など
    location_reason: str | None = None
    est_sales_low: int | None = None
    est_sales_mid: int | None = None
    est_sales_high: int | None = None
    flags: list[str] = field(default_factory=list)  # 賃料過大/要目視/2階/スケルトン/商圏未確定
    gmap_url: str = ""                   # https://maps.google.com/maps?q={住所}（座標は使わない・§9.1）
    streetview_urls: list[str] = field(default_factory=list)

    # --- 由来メタ ---
    listing_agent: str | None = None     # 掲載不動産会社名（§5.2 逆引きで使用・第3段階）
    raw_rent_text: str | None = None      # 家賃の原文（税別判定・要相談検知用）


@dataclass
class RawListing:
    """パーサがHTMLから抜いた素の値（正規化前）。sources/* が返す。

    面積・家賃・階・駐車場は「原文テキスト」で持ち、正規化は共通処理側で行う。
    こうすると単位取り違え（§9.1.1）の検査を1箇所に集約できる。
    """
    source_domain: str
    detail_url: str
    prefecture: str
    city: str
    name: str
    address: str
    area_text: str | None = None
    rent_text: str | None = None
    parking_text: str | None = None
    floor_text: str | None = None
    deposit_text: str | None = None
    key_money_text: str | None = None
    brokerage_text: str | None = None
    roadside_flag: bool | None = None
    listing_agent: str | None = None
    property_type: str | None = None      # 貸店舗/事務所/倉庫 等
