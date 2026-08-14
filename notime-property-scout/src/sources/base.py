"""ソース共通インターフェース（§5.3：ソース単位で例外を握りつぶすための土台）。"""
from __future__ import annotations

from typing import Protocol

from ..models import RawListing


class Source(Protocol):
    domain: str

    def list_urls(self, city: dict) -> list[str]:
        """対象市の一覧ページURL（複数ページ可）を、ゲート条件を反映して組む（§5.1.1）。"""
        ...

    def parse_listing(self, html: str, city: dict) -> list[RawListing]:
        """一覧ページHTMLから RawListing を抽出する。個別物件URLを必ず埋める（§9.1）。"""
        ...
