"""Reporter 共通インターフェース（§9）。

出力先はアダプタ方式（§9）。採点ロジック側に通知の都合を持ち込まない。
Slack/Supabase を足す場合も、この Protocol を実装して REPORTERS に足すだけにする。
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from ..models import Property


class RunSummary:
    """ヘッダのサマリ（§9.2）。収集件数／ゲート通過数／新規数／broken ソース一覧。"""

    def __init__(self):
        self.collected = 0
        self.gate_pass = 0
        self.new_count = 0
        self.broken_sources: list[str] = []
        self.parse_notes: list[str] = []
        self.dead_links = 0        # リンク生存確認で「掲載終了/不存在」だった件数
        self.unverified_links = 0  # 生存確認できなかった件数（取得失敗等）
        self.link_check_ran = False


class Reporter(Protocol):
    def emit(self, properties: list[Property], run_date: date, summary: "RunSummary") -> None:
        ...
