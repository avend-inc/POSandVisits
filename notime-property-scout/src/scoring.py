"""スコアリング（§3・100点）と想定月商レンジ（§3.1）、出力ランク（§3.4）。

スコアはゲート通過物件の「並び順」にのみ使う。出力列にはしない（§9.1）。
配点は便宜的なもので、合否は想定月商レンジと個別フラグを見て人が判断する（§13）。

MVP注記:
  集客装置12点（§3：チェーン飲食・セカスト）は Places（§7.2）未実装のため 0 点。
  したがってMVPの満点は実質 88 点（面積35+駐車25+坪単価20+取得費8）。
  第2段階で Places が入ると 12 点分が加算される。
"""
from __future__ import annotations

from .models import Property

RENT_OVER_RATIO = 5.0   # §3.1 mid/家賃 < 5.0 で「賃料過大」（失敗店は3.3〜4.5倍）


def score(p: Property) -> int:
    """§3 のスコア関数。ゲート通過物件のみ算出する前提。"""
    s = 0
    tsubo = p.area_tsubo or 0.0

    # 面積 35点 — 月商の上限を決める最重要因子
    if   tsubo >= 65: s += 35   # 福井水準（69.9坪・月商500万）
    elif tsubo >= 45: s += 33
    elif tsubo >= 39: s += 30   # 伊予松前水準
    elif tsubo >= 30: s += 26
    elif tsubo >= 25: s += 18   # 山形水準
    else:             s += 0    # ゲートで除外済み

    # 駐車場 25点 — 月商200万超の必要条件
    park = p.parking_slots or 0
    if   park >= 10: s += 25
    elif park >= 7:  s += 22    # いわき水準
    elif park >= 5:  s += 18    # 伊予松前・山形水準
    elif park >= 3:  s += 10    # 徳島水準
    elif park >= 2:  s += 5     # 旭川・富山水準（月商120万天井）
    else:            s += 0

    # 坪単価 20点 — 回収可能性
    if tsubo and p.rent_monthly:
        unit = p.rent_monthly / tsubo
        if   unit <= 5000:  s += 20   # 伊予松前・いわき水準
        elif unit <= 8000:  s += 16   # 山形水準
        elif unit <= 10000: s += 10   # 徳島・富山水準
        elif unit <= 12000: s += 4
        else:               s += 0

    # 集客装置 12点 — 「徳島型」の再現性（§7.2・MVPは None→0）
    if p.chain_restaurants_500m is not None:
        s += min(8, p.chain_restaurants_500m * 2)
    if p.secondstreet_within_2km:
        s += 4

    # 物件取得費 8点
    cost = p.acquisition_cost
    if cost is not None:
        if   cost <= 1_500_000: s += 8
        elif cost <= 2_000_000: s += 6
        elif cost <= 2_500_000: s += 3
        else:                   s += 0

    return s


def estimated_sales(p: Property) -> tuple[int, int, int]:
    """想定月商レンジ（§3.1）。坪効率レンジで low/mid/high を返す。"""
    t = p.area_tsubo or 0.0
    low  = int(t * 45000)    # いわき型（薄商圏で頭打ち）
    mid  = int(t * 72000)    # 徳島・富山・福井型
    high = int(t * 100000)   # 伊予松前・山形型
    return low, mid, high


def rent_overload(p: Property) -> bool:
    """回収チェック（§3.1）: mid / 家賃 < 5.0 なら賃料過大。"""
    if not p.rent_monthly:
        return False
    _, mid, _ = estimated_sales(p)
    return (mid / p.rent_monthly) < RENT_OVER_RATIO


def rank(p: Property) -> str:
    """出力ランク（§3.4）。

    A: 80点以上 かつ 賃料過大フラグなし / B: 65〜79 / C: 50〜64
    要確認: ゲート判定に必要なデータが欠損 / 除外: ゲート不通過
    §6.0.1 の商圏未確定市はランクAを出さない（Bへ丸める）。
    """
    if p.gate_result == "fail":
        return "除外"
    if p.gate_result == "incomplete":
        return "要確認"
    s = p.score if p.score is not None else 0
    over = "賃料過大" in p.flags
    if s >= 80 and not over:
        # 商圏未確定（§6.0.1）ならランクAを出さず B に丸める
        if "商圏未確定" in p.flags:
            return "B"
        return "A"
    if s >= 65:
        return "B"
    if s >= 50:
        return "C"
    return "C"
