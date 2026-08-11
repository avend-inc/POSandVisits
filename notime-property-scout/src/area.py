"""面積の取り扱い（§9.1.1・最重要）。

単位の取り違えが最も起きやすく最も影響が大きい（15坪と50㎡はほぼ同じ数値だが、
片方を取り違えると失敗店サイズの物件を成功店サイズとして通してしまう）。

方針（§9.1.1 / §13）:
  - 数値の隣接文字列から単位を必ず判定する。
  - **単位が特定できなければパースを失敗させる（推測で埋めない）。**
  - 内部は ㎡ と 坪 の両方を保持。換算は 1坪 = 3.30578㎡ 固定。
  - 帖/畳 は 1帖 = 1.62㎡ で換算。
  - registry の area_unit_hint と食い違ったら UnitMismatch。
"""
from __future__ import annotations

import re

TSUBO_TO_SQM = 3.30578

# §9.1.1 のとおり。㎡/m2/平米 は等価、坪/帖/畳 は換算係数。
UNIT_TO_SQM = {
    "㎡": 1.0,
    "m2": 1.0,
    "m²": 1.0,
    "平米": 1.0,
    "平方メートル": 1.0,
    "坪": TSUBO_TO_SQM,
    "帖": 1.62,
    "畳": 1.62,
}

# 単位トークン。長いものを先に並べて最長一致させる。
_UNIT_ALT = "|".join(
    re.escape(u) for u in sorted(UNIT_TO_SQM, key=len, reverse=True)
)
# 「32.5坪」「107.4㎡」「1,024 m2」等。数値の直後に単位が来る形だけ受理する。
_AREA_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(" + _UNIT_ALT + r")")


class ParseError(ValueError):
    """面積の単位を特定できない等、パースを失敗させるべき状態。"""


class UnitMismatch(ParseError):
    """registry の area_unit_hint と実際の単位が食い違った（§9.1.1：broken を立てる）。"""


def parse_area(text: str, hint: str | None = None) -> tuple[float, float]:
    """面積文字列を (area_sqm, area_tsubo) に変換する。

    単位が特定できなければ ParseError を送出（推測しない）。
    hint（registry の area_unit_hint）が与えられ、抽出単位と食い違えば UnitMismatch。

    帖/畳 で換算した場合の「（帖換算）」表記は呼び出し側で area_unit_raw を見て付す。
    """
    if text is None:
        raise ParseError("面積テキストが空です")
    m = _AREA_RE.search(str(text))
    if not m:
        raise ParseError(f"面積の単位を特定できません: {text!r}")
    value = float(m.group(1).replace(",", ""))
    unit = m.group(2)
    if unit == "m²":
        unit = "m2"
    if hint and unit != hint:
        raise UnitMismatch(f"期待={hint} 実際={unit} ({text!r})")
    sqm = value * UNIT_TO_SQM[unit]
    return sqm, sqm / TSUBO_TO_SQM


def sqm_to_tsubo(sqm: float) -> float:
    return sqm / TSUBO_TO_SQM


def tsubo_to_sqm(tsubo: float) -> float:
    return tsubo * TSUBO_TO_SQM


def format_area(area_tsubo: float, area_sqm: float, unit_raw: str | None = None) -> str:
    """出力表記（§9.1 列3 / §9.1.1）: 坪を主・㎡を括弧内。片方だけの表示はしない。

    帖/畳 由来なら「（帖換算）」を付す。
    """
    suffix = ""
    if unit_raw in ("帖", "畳"):
        suffix = "（帖換算）"
    return f"{area_tsubo:.1f}坪（{area_sqm:.1f}㎡）{suffix}"
