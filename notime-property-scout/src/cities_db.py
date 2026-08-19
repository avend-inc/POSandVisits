"""対象市区町村を Supabase の bukken_cities から読む。

レビュー画面（web/cities.html「対象エリア」）で登録した市を、収集パイプラインが
巡回対象として使う。テーブルが無い/取得できない場合は cities.yaml にフォールバック
する（収集を止めない）。

bukken_cities の1行は cities.yaml の1エントリと同じ形（name / pref / pref_roma /
jis / main_station / priority / warn）＋ enabled。enabled=false の市は巡回しない。
"""
from __future__ import annotations

import json
import os
import urllib.request

# cities.yaml エントリと揃えるキー
_KEYS = ("name", "pref", "pref_roma", "jis", "main_station", "priority", "warn")


def fetch(url: str | None = None, key: str | None = None) -> list[dict] | None:
    """bukken_cities（enabled のみ）を cities.yaml と同じ形の list[dict] で返す。

    取得できない/テーブル未作成/1件も無い場合は None を返す（呼び出し側が
    cities.yaml にフォールバックする）。
    """
    url = url or os.environ.get("SUPABASE_URL")
    key = key or os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        endpoint = (url.rstrip("/")
                    + "/rest/v1/bukken_cities?select=*&enabled=eq.true&order=priority,jis")
        req = urllib.request.Request(endpoint, headers={
            "apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # テーブル未作成・ネットワーク等 → cities.yaml へ
        print(f"bukken_cities取得に失敗（cities.yamlを使用）: {e}")
        return None
    if not rows:
        return None
    out: list[dict] = []
    for r in rows:
        city = {k: r.get(k) for k in _KEYS}
        # 型を cities.yaml と揃える
        if city.get("priority") is not None:
            city["priority"] = int(city["priority"])
        city["warn"] = bool(city.get("warn"))
        out.append(city)
    return out
