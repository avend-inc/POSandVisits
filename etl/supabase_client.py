"""
Supabase への読み書き

【方針】
  Supabase は「PostgREST」というHTTPの窓口を持っていて、
  普通のHTTPリクエストだけで読み書きできる。
  余計な部品を増やさないため、requests だけで書いている（supabase-py は不要）。

【二重登録を防ぐ仕組み（ここが一番大事）】
  行を送るときに
      POST /rest/v1/sales?on_conflict=store_id,pos_name,tx_id,line_no
      Prefer: resolution=ignore-duplicates
  を付ける。すると Supabase は
      「その組み合わせの行が既にあったら、何もせず無視する」
  という動きをする。だから同じCSVを何度流し込んでも行は増えない。

  さらに Prefer: return=representation を付けると
  「実際に新しく入った行」だけが返ってくるので、
  新規○件／重複無視○件 を正確に数えられる。
"""
from __future__ import annotations

import json
import math
from typing import Any, Iterable

import requests

from .settings import EtlError, SUPABASE_CHUNK, require_env

TIMEOUT = 60


def _clean(value: Any) -> Any:
    """JSONに載らない値（NaN / numpy型 など）を素直な値に直す。"""
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, bool):
        return value
    # numpy の数値・真偽値をPythonの型へ
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return _clean(value.item())
        except Exception:
            return str(value)
    return value


def _clean_rows(rows: Iterable[dict]) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()} for row in rows]


class Supabase:
    """Supabase（PostgREST）への最小限のクライアント。"""

    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or require_env(
            "SUPABASE_URL",
            "Supabaseの入口アドレス。例: https://rwfcsanmqvkxiuwdeddv.supabase.co",
        )).rstrip("/")
        self.key = key or require_env(
            "SUPABASE_SERVICE_KEY",
            "Supabase管理画面 → Project Settings → API Keys の service_role キー",
        )
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # --------------------------------------------------------
    # 低レベル
    # --------------------------------------------------------
    def _endpoint(self, table: str) -> str:
        return f"{self.url}/rest/v1/{table}"

    def _raise(self, resp: requests.Response, what: str) -> None:
        raise EtlError(
            f"Supabaseへの{what}に失敗しました（HTTP {resp.status_code}）。\n"
            f"  返答: {resp.text[:500]}\n"
            "  よくある原因:\n"
            "    ・SUPABASE_SERVICE_KEY が間違っている／期限切れ\n"
            "    ・sql/001_schema.sql をまだ実行していない（テーブルが無い）\n"
            "    ・SUPABASE_URL の綴り違い"
        )

    def select(self, table: str, params: dict[str, str]) -> list[dict]:
        resp = self.session.get(self._endpoint(table), params=params, timeout=TIMEOUT)
        if resp.status_code >= 400:
            self._raise(resp, f"読み出し（{table}）")
        return resp.json()

    def insert(self, table: str, rows: list[dict]) -> list[dict]:
        """普通の追加（重複が起きたらエラーになる）。"""
        resp = self.session.post(
            self._endpoint(table),
            data=json.dumps(_clean_rows(rows), ensure_ascii=False).encode("utf-8"),
            headers={"Prefer": "return=representation"},
            timeout=TIMEOUT,
        )
        if resp.status_code == 409:
            raise DuplicateError(resp.text[:300])
        if resp.status_code >= 400:
            self._raise(resp, f"書き込み（{table}）")
        return resp.json()

    def insert_ignore_duplicates(
        self, table: str, rows: list[dict], on_conflict: str
    ) -> tuple[int, int]:
        """
        重複を無視して追加する（冪等な取り込み）。

        戻り値: (新しく入った行数, 既にあったので無視した行数)
        """
        if not rows:
            return 0, 0

        inserted = 0
        for start in range(0, len(rows), SUPABASE_CHUNK):
            chunk = _clean_rows(rows[start:start + SUPABASE_CHUNK])
            resp = self.session.post(
                self._endpoint(table),
                params={"on_conflict": on_conflict},
                data=json.dumps(chunk, ensure_ascii=False).encode("utf-8"),
                headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                self._raise(resp, f"書き込み（{table}）")
            inserted += len(resp.json())

        return inserted, len(rows) - inserted

    # --------------------------------------------------------
    # stores（店舗マスタ）
    # --------------------------------------------------------
    def store_map(self) -> dict[str, int]:
        """店舗名 → id の対応表。"""
        rows = self.select("stores", {"select": "id,name"})
        return {r["name"]: r["id"] for r in rows}

    def get_or_create_store(self, name: str, cache: dict[str, int]) -> int:
        """
        店舗名からidを引く。知らない店舗なら自動で登録する。

        ⚠️ わざと「エラーで止める」ではなく「自動追加」にしている。
           新店舗が増えたときにデータを取りこぼす方が損失が大きいため。
           追加された場合は画面に注意書きを出す。
        """
        if name in cache:
            return cache[name]

        print(f"  ⚠️ 知らない店舗名『{name}』が出てきたので stores に自動追加します。"
              "（あとで sql/002_seed_stores.sql に追記しておくと綺麗です）")
        code = f"auto_{abs(hash(name)) % 10**8}"
        created = self.insert("stores", [{"code": code, "name": name}])
        store_id = created[0]["id"]
        cache[name] = store_id
        return store_id

    # --------------------------------------------------------
    # ingest_log（実行記録）
    # --------------------------------------------------------
    def already_succeeded(self, source: str, business_date: str,
                          store_id: int | None) -> bool:
        """その営業日を既に取り込み済みか？（再取り込みをNGにするための確認）"""
        params = {
            "select": "id",
            "source": f"eq.{source}",
            "business_date": f"eq.{business_date}",
            "status": "eq.success",
            "limit": "1",
        }
        params["store_id"] = "is.null" if store_id is None else f"eq.{store_id}"
        return len(self.select("ingest_log", params)) > 0

    def log(self, **row) -> None:
        """
        実行結果を1行記録する。

        status='success' は (source, 営業日, 店舗) につき1本しか作れない
        （sql/001_schema.sql の部分ユニークインデックス）。
        万一2本目を入れようとしたら、NGとして記録し直す。
        """
        try:
            self.insert("ingest_log", [row])
        except DuplicateError:
            fallback = dict(row)
            fallback["status"] = "rejected_duplicate"
            fallback["message"] = (
                "同じ営業日の取り込み記録が既にありました（二重取り込みを拒否）。"
                + (" / " + str(row.get("message")) if row.get("message") else "")
            )[:2000]
            self.insert("ingest_log", [fallback])
        except EtlError as e:
            # ログの書き込み失敗で本体を巻き込まない
            print(f"  ⚠️ ingest_log への記録に失敗しました: {e}")


class DuplicateError(Exception):
    """一意キー違反（既に同じ行がある）。"""
