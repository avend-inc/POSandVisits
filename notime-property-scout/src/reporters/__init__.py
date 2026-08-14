"""出力アダプタ（§9）。採点結果を受け取って書き出す共通IF。MVPは Html + Tsv。"""
from .base import Reporter
from .html import HtmlReporter
from .tsv import TsvReporter

__all__ = ["Reporter", "HtmlReporter", "TsvReporter"]
