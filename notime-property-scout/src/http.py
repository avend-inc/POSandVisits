"""収集マナーを守るHTTP取得（§5.4）。

- robots.txt を尊重
- 1リクエスト/3秒以上の間隔
- User-Agent に連絡先を明記
- 429/403 を受けたら該当ドメインを24時間クールダウン
- meta-robots: noarchive のため、HTMLはメモリ上で処理し保存しない（§5.1.1）

実行は手元Mac / 小規模VPS（§10）を前提とする。データセンターIPはボット判定で
遮断されるため、GitHub Actions等での取得はしないこと。
"""
from __future__ import annotations

import json
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

import requests

MIN_INTERVAL_SEC = 3.0
COOLDOWN_SEC = 24 * 60 * 60
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COOLDOWN_FILE = DATA_DIR / "cooldown.json"


class CooldownError(RuntimeError):
    """ドメインが24hクールダウン中（§5.4）。"""


class RobotsDisallowed(RuntimeError):
    """robots.txt が当該URLの取得を禁止している（§5.4）。"""


class Fetcher:
    def __init__(self, contact: str, user_agent: str | None = None):
        self.contact = contact
        self.ua = user_agent or f"notime-property-scout/0.1 (+{contact})"
        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.ua})
        self._cooldown = self._load_cooldown()

    # --- クールダウン永続化 ---
    def _load_cooldown(self) -> dict[str, float]:
        if COOLDOWN_FILE.exists():
            try:
                return json.loads(COOLDOWN_FILE.read_text())
            except Exception:
                return {}
        return {}

    def _save_cooldown(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        COOLDOWN_FILE.write_text(json.dumps(self._cooldown))

    def _in_cooldown(self, host: str) -> bool:
        until = self._cooldown.get(host)
        return until is not None and time.time() < until

    def _set_cooldown(self, host: str) -> None:
        self._cooldown[host] = time.time() + COOLDOWN_SEC
        self._save_cooldown()

    # --- robots ---
    def _robots_for(self, host: str, scheme: str) -> urllib.robotparser.RobotFileParser:
        rp = self._robots.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{scheme}://{host}/robots.txt")
            try:
                rp.read()
            except Exception:
                # robots取得失敗時は保守的に「読めていない」扱い。can_fetchはTrueを返しうるが、
                # ここではrobotsが読めなければ許可扱い（多くの実装と同じ）。ログは呼び出し側で。
                pass
            self._robots[host] = rp
        return rp

    def _respect_interval(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            wait = MIN_INTERVAL_SEC - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[host] = time.time()

    def _request(self, url: str, timeout: int = 30):
        """収集マナー（§5.4）を守って1回GETし、requestsのResponseを返す。

        robots禁止・クールダウン中は例外。403/429はクールダウンを立てる（が例外化はしない）。
        4xx/5xx でも raise せずResponseを返すので、ソフト404の本文判定（リンク生存確認）に使える。
        """
        parts = urlsplit(url)
        host = parts.netloc
        if self._in_cooldown(host):
            raise CooldownError(f"{host} は24時間クールダウン中（§5.4）")

        rp = self._robots_for(host, parts.scheme)
        if not rp.can_fetch(self.ua, url):
            raise RobotsDisallowed(f"robots.txt が取得を禁止: {url}")

        self._respect_interval(host)
        resp = self.session.get(url, timeout=timeout)
        if resp.status_code in (403, 429):
            self._set_cooldown(host)
        resp.encoding = resp.apparent_encoding or resp.encoding
        return resp

    def get(self, url: str, timeout: int = 30) -> str:
        """URLを取得して本文テキストを返す。HTMLは保存しない（noarchive・§5.1.1）。"""
        resp = self._request(url, timeout=timeout)
        resp.raise_for_status()
        return resp.text

    def status_and_body(self, url: str, timeout: int = 20) -> tuple[int, str]:
        """(HTTPステータス, 本文) を返す。4xx/5xxでも例外化しない（リンク生存確認用）。"""
        resp = self._request(url, timeout=timeout)
        return resp.status_code, resp.text
