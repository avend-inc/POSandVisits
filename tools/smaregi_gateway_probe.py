"""
スマレジのログイン画面(/control/Main.html)が403で拒否される件の切り分け用。

ログインは一切試みず、実データ取得に使っているAJAXゲートウェイURLへ
未認証のまま1回だけアクセスして、応答（ステータスコード・本文の先頭）を見る。

・403等でブロックされていれば → ゲートウェイ自体もアクセス元ごと拒否されている
  （＝セッションを使い回す方法も効かない可能性が高い）
・「未ログイン」等のエラーJSON/HTMLが返れば → ゲートウェイはブロックされておらず、
  ログイン画面だけが狙われている（＝セッション使い回しが効く見込みがある）

使い方: python -m tools.smaregi_gateway_probe
"""
from __future__ import annotations

import requests

GATEWAY = "https://www1.smaregi.jp/services/ajax/gateway.php"


def main() -> int:
    resp = requests.get(
        GATEWAY,
        params={
            "service": "AjaxMainService",
            "method": "getSalesInfo",
            "params": '{"viewMode":"1","searchDate":"2026/08/24"}',
        },
        timeout=20,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
        },
    )
    print(f"status: {resp.status_code}")
    print(f"content-type: {resp.headers.get('content-type')}")
    print("body head (先頭500字):")
    print(resp.text[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
