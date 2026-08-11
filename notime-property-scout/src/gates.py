"""必須ゲート（§2）。1つでも欠けたら除外し、スコア計算しない。

MVP注記:
  G4（幹線道路から50m以内）は本来 OSM `highway` タグ（§7.1）で判定するが、MVPは
  §7未実装のため、掲載元の「路面店」「幹線道路沿い」フラグ（roadside_flag）で暫定判定する
  （ユーザー指示）。フラグが取れない場合は fail ではなく incomplete（要確認）に落とす。
  OSMが無い状態で「幹線でない」と断定はできないため（§2.2 の未記載＝取りこぼさない思想）。

未記載データの扱い（§2.2）:
  - 面積未記載        → 除外（判定不能）
  - 駐車場未記載      → 除外せず「要確認」（incomplete）
  - 家賃未記載(要相談) → 「要確認」（incomplete）
"""
from __future__ import annotations

from dataclasses import dataclass

from .area import TSUBO_TO_SQM
from .models import Property

# ゲート閾値（§2）
MIN_TSUBO = 25.0            # G1: 25坪（約83㎡）
MIN_PARKING = 2            # G3: 2台以上
MAX_UNIT_PRICE = 12000     # G5: 坪単価 ≤ 12,000円
MAX_ACQUISITION = 3_000_000  # G6: 物件取得費 ≤ 300万円
ALLOWED_FLOORS = (1, 2)    # G2: 1階 または 2階


@dataclass
class GateOutcome:
    result: str            # pass | fail | incomplete
    failed_on: list[str]   # 明確に fail したゲート
    incomplete_on: list[str]  # データ欠損で判定できないゲート（要確認）
    flags: list[str]       # 2階 / 要目視 / スケルトン 等、判定に付随するフラグ


def evaluate_gates(p: Property) -> GateOutcome:
    failed: list[str] = []
    incomplete: list[str] = []
    flags: list[str] = []

    # G1 面積 ≥ 25坪 -----------------------------------------------------------
    if p.area_tsubo is None:
        # 面積未記載は除外（§2.2）。判定不能なので fail 扱い（＝収集はするがランク除外）。
        failed.append("G1(面積未記載→除外)")
    elif p.area_tsubo < MIN_TSUBO:
        failed.append(f"G1(面積{p.area_tsubo:.1f}坪<25坪)")

    # G2 階数 = 1 または 2 -----------------------------------------------------
    if p.floor is None:
        incomplete.append("G2(階数未記載)")
    elif p.floor >= 3:
        failed.append(f"G2(階数{p.floor}階≥3階)")
    elif p.floor == 2:
        # 2階は許容するが自動判定不可。要目視フラグ（§2.1）。
        flags.append("2階")
        flags.append("要目視")

    # G3 駐車場 ≥ 2台、または近隣確保見込み -----------------------------------
    if p.parking_slots is None:
        incomplete.append("G3(駐車場未記載)")   # 除外せず要確認（§2.2）
    elif p.parking_slots < MIN_PARKING:
        failed.append(f"G3(駐車場{p.parking_slots}台<2台)")

    # G4 幹線道路から50m以内（MVP: 路面店/幹線フラグで暫定） --------------------
    if p.road_class is not None or p.distance_to_trunk_m is not None:
        # 第2段階でOSMが入ったらこちらを正とする。
        ok = (p.road_class in ("trunk", "primary", "secondary")) or (
            p.distance_to_trunk_m is not None and p.distance_to_trunk_m <= 50
        )
        if not ok:
            failed.append("G4(幹線道路から50m超)")
    elif p.roadside_flag is True:
        pass  # 掲載元フラグで暫定通過
    else:
        # フラグ無し。OSM未取得のため「幹線でない」と断定できない → 要確認。
        incomplete.append("G4(幹線道路判定データなし)")

    # G5 坪単価 ≤ 12,000円 -----------------------------------------------------
    if p.rent_monthly is None:
        incomplete.append("G5(家賃未記載)")     # 要相談等 → 要確認（§2.2）
    elif p.area_tsubo:
        unit = p.rent_monthly / p.area_tsubo
        if unit > MAX_UNIT_PRICE:
            failed.append(f"G5(坪単価{unit:,.0f}円>12,000円)")

    # G6 物件取得費 ≤ 300万円 --------------------------------------------------
    if p.acquisition_cost is None:
        incomplete.append("G6(取得費算出不可)")
    elif p.acquisition_cost > MAX_ACQUISITION:
        failed.append(f"G6(取得費{p.acquisition_cost:,}円>300万円)")

    # 総合判定 -----------------------------------------------------------------
    if failed:
        result = "fail"
    elif incomplete:
        result = "incomplete"
    else:
        result = "pass"

    return GateOutcome(result=result, failed_on=failed, incomplete_on=incomplete, flags=flags)
