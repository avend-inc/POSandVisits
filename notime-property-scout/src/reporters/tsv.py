"""TSV出力（§9.3・副出力）。out/YYYY-MM-DD.tsv に9列をタブ区切り。ヘッダ行は含めない。

スプレッドシートへそのまま貼り付けられる状態にする。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..models import Property
from .base import RunSummary
from .columns import row9

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "out"


def _clean(cell: str) -> str:
    # タブ/改行はセルを壊すため空白へ
    return str(cell).replace("\t", " ").replace("\n", " ").replace("\r", " ")


class TsvReporter:
    def __init__(self, out_dir: Path | str = OUT_DIR):
        self.out_dir = Path(out_dir)

    def emit(self, properties: list[Property], run_date: date, summary: RunSummary) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{run_date.isoformat()}.tsv"
        lines = []
        # 除外（ゲート不通過）は貼り付け対象から外し、要確認は末尾に置く並びにする。
        rows = [p for p in properties if p.rank != "除外"]
        for p in rows:
            lines.append("\t".join(_clean(c) for c in row9(p)))
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
