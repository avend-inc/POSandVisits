"""AI評価＝立地類似判定（§3.3）。スコア（§3）とは別軸。

**立地だけ**を見て成功店の立地に似ているかを判定する。面積・家賃・坪単価は一切使わない（§13）。
判定は3分類に揃える：成功店類似 / 失敗店類似 / 判定不可。

MVP注記（重要）:
  接道（OSM・§7.1）と集客装置（Places・§7.2）のデータが無いため、本システムは
  **全件「判定不可」を返す**（ユーザー指示）。ロジックの器とパターン辞書はここに実装しておき、
  第2段階で road_class / chains_500m 等が埋まれば自動的に判定が走る。

必ず「判定不可」を返す条件（§3.3）:
  - OSM接道判定が取れていない
  - Places の集客装置データが取れていない
  - 商圏側に未解決フラグがある市（§6.0.1）→ 判定不可（商圏未確定）とし、成功店類似を出さない
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Property

# 接道クラス集合（§3.3 / §7.1）
TRUNK = {"trunk", "primary"}
SECONDARY = {"secondary"}
MINOR = {"tertiary", "unclassified"}
RESIDENTIAL = {"residential", "living_street"}

SUCCESS = "成功店類似"
FAILURE = "失敗店類似"
UNKNOWN = "判定不可"


@dataclass
class LocView:
    """立地変数だけを集めたビュー（面積・家賃は含めない・§13）。"""
    road: str | None
    dist_to_trunk_m: float | None
    roadside_flag: bool | None
    chains_500m: int | None
    large_sc_500m: bool | None
    secondstreet_2km: bool | None
    in_shopping_arcade: bool | None
    station_km: float | None
    parking: int | None

    @classmethod
    def of(cls, p: Property) -> "LocView":
        return cls(
            road=p.road_class,
            dist_to_trunk_m=p.distance_to_trunk_m,
            roadside_flag=p.roadside_flag,
            chains_500m=p.chain_restaurants_500m,
            large_sc_500m=p.large_sc_500m,
            secondstreet_2km=p.secondstreet_within_2km,
            in_shopping_arcade=p.in_shopping_arcade,
            station_km=p.station_distance_km,
            parking=p.parking_slots,
        )


def _z(v):
    """None を安全な既定（数値0 / False）に落とすヘルパ。パターン判定内でのみ使用。"""
    return 0 if v is None else v


# 立地パターン辞書（§3.3）。(名前, 説明, kind, 判定関数)。kind: SUCCESS|FAILURE
LOCATION_PATTERNS = [
    # --- 成功店類似 ---
    ("徳島沖浜型", "幹線沿い＋チェーン飲食集積＋駅から距離あり", SUCCESS,
     lambda p: p.road in TRUNK and _z(p.chains_500m) >= 3 and _z(p.station_km) >= 2.0),
    ("山形型", "国道沿い＋セカスト近接", SUCCESS,
     lambda p: p.road in TRUNK and bool(p.secondstreet_2km) and _z(p.chains_500m) >= 2),
    ("伊予松前型", "大型SCへの寄生立地", SUCCESS,
     lambda p: p.road in TRUNK and bool(p.large_sc_500m)),
    ("いわき型", "幹線街道沿い・若者動線上", SUCCESS,
     lambda p: p.road in TRUNK and _z(p.station_km) >= 3.0 and _z(p.chains_500m) >= 2),
    ("福井型", "郊外幹線・大規模駐車可能な独立立地", SUCCESS,
     lambda p: p.road in TRUNK and _z(p.station_km) >= 2.0 and _z(p.parking) >= 10),
    ("旭川・富山型", "市街地寄りの幹線沿い", SUCCESS,
     lambda p: p.road in TRUNK and _z(p.station_km) < 2.0 and _z(p.chains_500m) >= 2),
    # --- 失敗店類似 ---
    ("仙台吉成型", "幹線から外れた住宅地内", FAILURE,
     lambda p: (p.road in RESIDENTIAL) or _z(p.dist_to_trunk_m) > 150),
    ("商店街・路地裏型", "商店街の中／大通りから一本裏", FAILURE,
     lambda p: bool(p.in_shopping_arcade) or (p.road in MINOR and not bool(p.roadside_flag))),
    ("集客装置ゼロ型", "周辺500mに集客装置なし", FAILURE,
     lambda p: _z(p.chains_500m) == 0 and not bool(p.large_sc_500m) and not bool(p.secondstreet_2km)),
]


@dataclass
class LocationVerdict:
    verdict: str            # 成功店類似 | 失敗店類似 | 判定不可
    pattern: str | None
    reason: str


def _location_data_available(p: Property) -> bool:
    """接道（OSM）と集客装置（Places）が最低限そろっているか。MVPは常に False。"""
    has_road = p.road_class is not None or p.distance_to_trunk_m is not None
    has_places = p.chain_restaurants_500m is not None or p.large_sc_500m is not None
    return has_road and has_places


def evaluate_location(p: Property, city_warn: bool = False) -> LocationVerdict:
    """立地類似判定（§3.3）。

    city_warn=True は §6.0.1 の商圏未解決市。この場合は「判定不可（商圏未確定）」を返し、
    成功店類似は出さない。
    """
    if city_warn:
        return LocationVerdict(UNKNOWN, None, "商圏未確定")

    if not _location_data_available(p):
        # MVPは §7 未実装のためここに落ちる（全件「判定不可」）。
        missing = []
        if p.road_class is None and p.distance_to_trunk_m is None:
            missing.append("接道データ未取得")
        if p.chain_restaurants_500m is None and p.large_sc_500m is None:
            missing.append("集客装置データ未取得")
        return LocationVerdict(UNKNOWN, None, "・".join(missing) or "立地データ未取得")

    view = LocView.of(p)
    matches = [(name, desc, kind) for (name, desc, kind, fn) in LOCATION_PATTERNS if fn(view)]
    if not matches:
        return LocationVerdict(UNKNOWN, None, "既知パターンに一致せず")

    # 複数該当なら失敗側を優先（安全側に倒す・§3.3）
    failures = [m for m in matches if m[2] == FAILURE]
    chosen = failures[0] if failures else matches[0]
    name, desc, kind = chosen
    return LocationVerdict(kind, name, desc)
