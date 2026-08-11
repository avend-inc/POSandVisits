"""住所正規化と重複キー（§8.1）。

物件名では絶対に判定しない（表記ゆれが激しく、同一物件が別名で複数サイトに載る）。

MVP注記:
  §8.1 の正規キーは normalize_address(address) + round(lat,5),round(lng,5) のハッシュだが、
  MVPは§7のジオコーディングを行わないため lat/lng が無い。よって MVP は住所のみで
  キーを作る（city を前置してごく稀な同一住所の市跨ぎ衝突を避ける）。lat/lng が
  埋まる第2段階では §8.1 どおり座標込みのキーへ自動的に切り替わる。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# 「大字」「字」など、住所の意味を変えない接頭辞を吸収する（§8.1）。
_DROP_TOKENS = ("大字", "字")
_SPACE_RE = re.compile(r"\s+")


def normalize_address(address: str) -> str:
    """住所を正規化する。

    - 全角/半角・丁目番地の表記ゆれを NFKC で吸収
    - 「大字」「字」を除去
    - 空白除去
    完全な正規化は normalize-japanese-addresses 相当が必要（第2段階）。ここは
    MVPの軽量版で、丁目・番地の全半角と大字有無を吸収する範囲に留める。
    """
    if not address:
        return ""
    s = unicodedata.normalize("NFKC", address)
    s = s.replace("ヶ", "ケ").replace("ヵ", "カ")
    for t in _DROP_TOKENS:
        s = s.replace(t, "")
    s = _SPACE_RE.sub("", s)
    # ハイフン類を統一（丁目番地の区切り）
    s = re.sub(r"[‐‑‒–—―ー−]", "-", s)
    return s.strip()


def property_key(
    address: str,
    lat: float | None = None,
    lng: float | None = None,
    city: str | None = None,
) -> str:
    """重複判定キー（§8.1）。lat/lng があれば座標込み、無ければ住所のみ（MVP）。"""
    base = normalize_address(address)
    if lat is not None and lng is not None:
        base = f"{base}{lat:.5f},{lng:.5f}"
    elif city:
        base = f"{city}|{base}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()
