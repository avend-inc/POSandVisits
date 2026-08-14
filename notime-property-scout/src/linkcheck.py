"""物件リンクの生存確認。

掲載終了で詳細ページが消えることは頻繁にある。連合隊(RALSNET)は詳細が消えても
**HTTP 200 のまま本文だけ「該当の物件が存在しません」を返すソフト404**なので、
ステータスコードだけでは死活判定できない。本文マーカーで判定する。

- alive : 生存（レポートに載せる）
- dead  : 掲載終了／存在しない（レポートから除外し「リンク切れ」フラグ）
- error : 取得失敗（robots禁止・クールダウン・タイムアウト等。判断保留＝要確認扱い）
"""
from __future__ import annotations

# 本文に含まれていたら「掲載終了/不存在」とみなすマーカー（連合隊/一般）。
DEAD_MARKERS = (
    "該当の物件が存在しません",
    "物件が存在しません",
    "ページが存在しません",
    "お探しの物件は見つかりません",
    "掲載が終了",
    "公開を終了",
)

ALIVE = "alive"
DEAD = "dead"
ERROR = "error"


def is_dead_html(html: str) -> bool:
    """本文がソフト404（掲載終了/不存在）かどうかを判定する。"""
    if not html:
        return False
    return any(m in html for m in DEAD_MARKERS)


def check_url(fetcher, url: str) -> str:
    """個別物件URLの生存を確認して alive/dead/error を返す。§5.4の間隔・robotsは fetcher が守る。"""
    try:
        status, body = fetcher.status_and_body(url)
    except Exception:
        return ERROR
    if status == 404 or status == 410:
        return DEAD
    if status >= 400:
        return ERROR
    return DEAD if is_dead_html(body) else ALIVE
