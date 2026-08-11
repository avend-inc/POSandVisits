"""SQLite 保存と差分検知（§10 / §11）。単一ファイル data/properties.db。

差分検知（§6）: 既知物件DBと突合し、新規（first_seen が本日）だけを抽出する。
再掲載で first_seen を上書きしない（§9.1 列1）。last_seen のみ更新する。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path

from .models import Property

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "properties.db"

# JSON化して保持するリスト/複合フィールド
_JSON_FIELDS = {"gate_failed_on", "flags", "streetview_urls"}
_DATE_FIELDS = {"first_seen", "last_seen"}


def _columns() -> list[str]:
    return [f.name for f in fields(Property)]


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        cols = _columns()
        coldefs = ",\n  ".join(f'"{c}" TEXT' for c in cols if c != "id")
        self.conn.execute(
            f'CREATE TABLE IF NOT EXISTS properties (\n'
            f'  "id" TEXT PRIMARY KEY,\n  {coldefs}\n)'
        )
        self.conn.commit()

    # --- 変換 ---
    def _to_row(self, p: Property) -> dict:
        d = asdict(p)
        for k in _JSON_FIELDS:
            d[k] = json.dumps(d.get(k) or [], ensure_ascii=False)
        for k in _DATE_FIELDS:
            v = d.get(k)
            d[k] = v.isoformat() if isinstance(v, date) else v
        return d

    def get(self, pid: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM properties WHERE id=?", (pid,))
        return cur.fetchone()

    def upsert(self, p: Property, today: date) -> bool:
        """物件を保存。新規なら True。既知なら first_seen を保持し last_seen 等を更新して False。"""
        existing = self.get(p.id)
        is_new = existing is None
        if not is_new and existing["first_seen"]:
            # 再掲載で first_seen を上書きしない（§9.1 列1）
            p.first_seen = date.fromisoformat(existing["first_seen"])
        p.last_seen = today
        row = self._to_row(p)
        cols = list(row.keys())
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f'"{c}"=excluded."{c}"' for c in cols if c != "id")
        self.conn.execute(
            f'INSERT INTO properties ({",".join(chr(34)+c+chr(34) for c in cols)}) '
            f'VALUES ({placeholders}) '
            f'ON CONFLICT(id) DO UPDATE SET {updates}',
            [row[c] for c in cols],
        )
        self.conn.commit()
        return is_new

    def save_all(self, props: list[Property], today: date) -> list[Property]:
        """全件保存し、今回新規だった Property のリストを返す（差分検知・§6）。"""
        new_props = []
        for p in props:
            if self.upsert(p, today):
                new_props.append(p)
        return new_props

    def close(self) -> None:
        self.conn.close()
