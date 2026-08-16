"""候補物件の「検索条件（しきい値）」をSupabaseの bukken_criteria から読む。

レビュー画面（web/bukken.html の⚙設定）で編集した条件を、収集(discover)・同期(sync)・
表示(レビュー画面) の全部で同じ値として使うための共通ロジック。
テーブルが無い/取得できない場合は DEFAULTS を使う（収集を止めない）。

条件の意味（面積・家賃・階数・駐車場が「判明していて範囲外」なら候補から除外する）:
  area_min / area_max : 面積(坪) の下限・上限
  rent_min / rent_max : 家賃(月額円) の下限・上限
  floor_max           : 所在階の上限（これ超は除外。1・2階のみなら 2）
  parking_min         : 駐車場台数の下限（0 なら不問）
"""
from __future__ import annotations

import json
import os
import urllib.request

# 既定値（設定画面で変更するまで、またはテーブル未作成時に使う）
DEFAULTS = {
    "area_min": 25.0,
    "area_max": 50.0,
    "rent_min": 30000,
    "rent_max": 500000,
    "floor_max": 2,
    "parking_min": 0,
}

# 数値として扱うキー（DBから来た値の型を揃える）
_NUMERIC = set(DEFAULTS)


def fetch(url: str | None = None, key: str | None = None) -> dict:
    """bukken_criteria の1行を取得して条件dictを返す。失敗時は DEFAULTS。"""
    url = url or os.environ.get("SUPABASE_URL")
    key = key or os.environ.get("SUPABASE_SERVICE_KEY")
    crit = dict(DEFAULTS)
    if not url or not key:
        return crit
    try:
        endpoint = url.rstrip("/") + "/rest/v1/bukken_criteria?select=*&limit=1"
        req = urllib.request.Request(endpoint, headers={
            "apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        if rows:
            for k in DEFAULTS:
                v = rows[0].get(k)
                if v is not None:
                    crit[k] = float(v) if k in ("area_min", "area_max") else int(v)
    except Exception as e:  # テーブル未作成・ネットワーク等 → 既定値で続行
        print(f"criteria取得に失敗（既定値を使用）: {e}")
    return crit


def out_of_range(rec: dict, crit: dict) -> bool:
    """判明している属性が条件レンジ外なら True（＝条件に合致しないと確定＝候補にしない）。

    値が不明(None)の属性は判定しない（ユーザーが確認すればレンジ内になりうるので残す）。
    """
    at = rec.get("area_tsubo")
    if at is not None and (at < crit["area_min"] or at > crit["area_max"]):
        return True
    rent = rec.get("rent_yen")
    if rent is not None and (rent < crit["rent_min"] or rent > crit["rent_max"]):
        return True
    fl = rec.get("floor")
    if fl is not None and fl > crit["floor_max"]:
        return True
    pk = rec.get("parking")
    if crit["parking_min"] and pk is not None and pk < crit["parking_min"]:
        return True
    return False
