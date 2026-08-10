"""
Airレジ「データ連携API」を叩くための実装（＋仕様が無いときの調査モード）。

いまは公式の技術ドキュメントが手元に無い。そこで、まず GitHub Actions 上
（＝制限のない普通のインターネット）で

  python -m etl.airregi_api --probe

を動かし、
  1. Airレジ公式FAQ（Zendeskの公開JSON）から「データ連携API」記事の本文を取得して表示
     → 実際の連携手順・エンドポイント・リンクを洗い出す（本文からURLも自動抽出）。
  2. 登録済みの AIRREGI_API_KEY / AIRREGI_API_TOKEN で候補エンドポイントを実際に叩き、
     ステータス・ヘッダ・本文の先頭をすべてダンプする。

その出力を見て、本物のエンドポイント／認証方式／レスポンス形を確定し、
下の fetch_range() を本実装する（会計明細 or 日次集計 → adapters へ）。

キー・トークンはソースに書かない。GitHub Secrets（AIRREGI_API_KEY / AIRREGI_API_TOKEN）
から環境変数で受け取る。ログにも生値は出さない（マスクして表示）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 「APIによる連携サービス」セクション（この配下に手順・一覧・お知らせ記事がある）
FAQ_SECTION_ID = "49876452946073"
# 直接見たい記事ID（セクション一覧が取れなくても本文を拾えるように）
FAQ_ARTICLE_IDS = [
    "48202752451353",  # APIで各種システムとデータ連携して利用する方法
    "48210229202329",  # APIでデータ連携できる各種システム一覧
    "48886324178841",  # 本部システムとAirレジとの連携を開始します
    "49169373074073",  # 2025/8/19 リリース情報
]

# 叩いてみる候補（ホスト×パス）。当たり（200/401/JSONエラー）が出た所を手掛かりにする。
CANDIDATE_HOSTS = [
    "https://connect.airregi.jp",
    "https://api.airregi.jp",
    "https://open.airregi.jp",
    "https://opendata.airregi.jp",
    "https://data.airregi.jp",
    "https://mp.airregi.jp",
    "https://airregi.jp",
    "https://connect.airmate.jp",
    "https://connect.airregi.jp/api",
]
CANDIDATE_PATHS = [
    "/", "/v1", "/v1/", "/api/v1", "/api/v1/",
    "/v1/sales", "/v1/sales/aggregations", "/v1/salesAggregations",
    "/v1/journals", "/v1/transactions", "/v1/deals",
    "/v1/stores", "/v1/shops", "/v1/me", "/v1/companies",
    "/api/v1/sales", "/api/v1/journals",
]


def _mask(v: str | None) -> str:
    if not v:
        return "(未設定)"
    v = v.strip()
    if len(v) <= 6:
        return v[:2] + "…"
    return f"{v[:4]}…{v[-3:]}（長さ{len(v)}）"


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</(p|div|li|tr|h[1-6])>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _extract_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s\"'<>）)]+", text)
    keep = []
    seen = set()
    for u in urls:
        u = u.rstrip(".,、。")
        if u in seen:
            continue
        seen.add(u)
        low = u.lower()
        if any(k in low for k in ("airregi", "airmate", "recruit", "api", "connect",
                                  "developer", "docs", "swagger", "openapi")):
            keep.append(u)
    return keep


# ------------------------------------------------------------
# 1) 公式FAQの本文を取得して表示（Actions上＝制限なしインターネットで動かす想定）
# ------------------------------------------------------------
def dump_faq() -> list[str]:
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept": "application/json"})
    found_urls: list[str] = []

    # まずセクション配下の記事一覧（全記事を機械的に拾える）
    ids = list(FAQ_ARTICLE_IDS)
    try:
        u = (f"https://faq.airregi.jp/api/v2/help_center/ja/sections/"
             f"{FAQ_SECTION_ID}/articles.json?per_page=100")
        r = sess.get(u, timeout=20)
        print(f"  [FAQ一覧] GET {u} -> HTTP {r.status_code}")
        if r.ok:
            data = r.json()
            for a in data.get("articles", []):
                aid = str(a.get("id"))
                if aid not in ids:
                    ids.append(aid)
                print(f"     ・記事 {aid}: {a.get('title')}")
    except Exception as e:
        print(f"  [FAQ一覧] 取得失敗: {e}")

    for aid in ids:
        try:
            u = f"https://faq.airregi.jp/api/v2/help_center/ja/articles/{aid}.json"
            r = sess.get(u, timeout=20)
            print(f"\n  ===== 記事 {aid} : HTTP {r.status_code} =====")
            if not r.ok:
                continue
            art = r.json().get("article", {})
            title = art.get("title", "")
            body = _strip_html(art.get("body", "") or "")
            print(f"  タイトル: {title}")
            print("  --- 本文 ---")
            print("\n".join("    " + ln for ln in body.splitlines()))
            urls = _extract_urls((art.get("body", "") or "") + "\n" + body)
            if urls:
                print("  --- 本文中のURL（候補） ---")
                for x in urls:
                    print(f"    {x}")
                found_urls.extend(urls)
        except Exception as e:
            print(f"  記事 {aid} 取得失敗: {e}")

    # 重複除去
    uniq, seen = [], set()
    for u in found_urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


# ------------------------------------------------------------
# 2) キー＋トークンで候補エンドポイントを叩いてダンプ
# ------------------------------------------------------------
def _auth_variants(key: str, token: str) -> list[tuple[str, dict]]:
    """考えられる認証ヘッダの付け方を列挙（当たりを1回の探索で見つけるため）。"""
    return [
        ("Bearer(token)", {"Authorization": f"Bearer {token}"}),
        ("Bearer(key)", {"Authorization": f"Bearer {key}"}),
        ("key+token headers", {
            "X-Airregi-Api-Key": key, "X-Airregi-Api-Token": token,
            "X-Api-Key": key, "X-Api-Token": token,
            "apikey": key, "apitoken": token,
        }),
        ("Authorization: key, X-Token: token", {
            "Authorization": key, "X-Airregi-Api-Token": token,
        }),
    ]


def probe_endpoints(extra_urls: list[str] | None = None) -> None:
    key = (os.environ.get("AIRREGI_API_KEY") or "").strip()
    token = (os.environ.get("AIRREGI_API_TOKEN") or "").strip()
    print("\n" + "=" * 60)
    print("【エンドポイント探索】")
    print(f"  AIRREGI_API_KEY   = {_mask(key)}")
    print(f"  AIRREGI_API_TOKEN = {_mask(token)}")
    if not key and not token:
        print("  ⚠️ キー・トークンが未設定です。Secretsに AIRREGI_API_KEY / "
              "AIRREGI_API_TOKEN を登録してください。認証なしの応答だけ確認します。")

    # 叩く先の一覧を組み立てる（候補ホスト×パス＋FAQ本文から拾ったURL）
    targets: list[str] = []
    for host in CANDIDATE_HOSTS:
        for path in CANDIDATE_PATHS:
            targets.append(host.rstrip("/") + path)
    for u in (extra_urls or []):
        targets.append(u)

    # ホスト単位で「そもそも到達するか」を先に見る（DNS/TLS）。到達しない所は深追いしない。
    reachable_hosts: set[str] = set()
    variants = _auth_variants(key, token)
    seen_targets: set[str] = set()
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept": "application/json"})

    printed = 0
    for url in targets:
        if url in seen_targets:
            continue
        seen_targets.add(url)
        host = re.sub(r"^(https?://[^/]+).*$", r"\1", url)
        # 未到達ホストのパス総当りは省略（DNS失敗が分かっている所）
        if host in _dead_hosts:
            continue
        # 認証パターンは「まず全部盛り」で1回。200/401/403やJSONが返れば次段で切り分け。
        merged = {}
        for _, hd in variants:
            merged.update(hd)
        try:
            r = sess.get(url, headers=merged, timeout=12, allow_redirects=False)
        except requests.exceptions.ConnectionError as e:
            msg = str(e)
            if "Name or service not known" in msg or "nodename nor servname" in msg \
               or "Failed to resolve" in msg:
                _dead_hosts.add(host)
                print(f"  [DNS×] {host} 解決できず（このホストは省略）")
            else:
                print(f"  [接続×] {url} : {msg[:120]}")
            continue
        except Exception as e:
            print(f"  [err] {url} : {type(e).__name__}: {str(e)[:120]}")
            continue

        reachable_hosts.add(host)
        ct = r.headers.get("content-type", "")
        loc = r.headers.get("location", "")
        body = ""
        try:
            body = r.text[:400].replace("\n", " ")
        except Exception:
            body = "(本文取得不可)"
        flag = ""
        if r.status_code in (200, 201) and ("json" in ct.lower()):
            flag = "  ★JSON応答"
        elif r.status_code in (401, 403):
            flag = "  ←認証まで到達（ホスト/パスは有効の可能性）"
        print(f"  [{r.status_code}] {url}  CT={ct} {('→'+loc) if loc else ''}{flag}")
        if r.status_code != 404 and body.strip():
            print(f"        body: {body}")
        printed += 1

    print(f"\n  到達できたホスト: {sorted(reachable_hosts) or '（なし）'}")
    print("  ※ ★JSON応答 or 401/403 が出たURLが本命。無ければFAQ本文のURL/手順を確認。")


_dead_hosts: set[str] = set()


# ------------------------------------------------------------
# 本実装用の入口（エンドポイント確定後に中身を書く）
# ------------------------------------------------------------
def fetch_range(start_date: str, end_date: str) -> str:
    """start_date〜end_date の売上データをAPIで取得して返す（確定後に実装）。"""
    raise NotImplementedError(
        "エンドポイント確定後に実装します。まず `--probe` で仕様を洗い出してください。")


def main() -> int:
    ap = argparse.ArgumentParser(description="Airレジ データ連携API（調査＋本実装）")
    ap.add_argument("--probe", action="store_true",
                    help="FAQ本文の取得＋候補エンドポイントの総当りダンプ（仕様洗い出し）")
    ap.add_argument("--faq-only", action="store_true", help="FAQ本文の取得だけ")
    args = ap.parse_args()

    if args.faq_only:
        dump_faq()
        return 0
    if args.probe:
        print("=" * 60)
        print("【FAQ本文の取得（実手順・エンドポイントの洗い出し）】")
        print("=" * 60)
        urls = dump_faq()
        probe_endpoints(extra_urls=urls)
        return 0

    print("使い方: --probe（FAQ取得＋エンドポイント探索） / --faq-only")
    return 2


if __name__ == "__main__":
    sys.exit(main())
