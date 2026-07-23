"""
デジテールストア 入店データ取得（山形・いわき・福井）

【このファイルがやること】
  1. デジテールにログインする
  2. 3店舗の入店データCSVをダウンロードする
  3. 生CSVを raw/digitail/ に保存する（加工前・復旧用）
  4. transform.normalize_entry() で正規化する
  5. db.import_entries() でDBに保存する

【実データで確認済みの仕様】（2026-07-22 に実際のサイトで確認）
  ログイン : POST https://dashboard.digitail-tech.com/auth/login
             フォーム項目は username / password の2つだけ。
             ⚠️ 設計書§5には name="email" と書かれていたが、実際は name="username"。
             隠しトークン（CSRF）は無い → ブラウザ無しでログインできる。

  CSV取得 : GET /{スラッグ}/kpi/visits/download?interval=day&from=YYYY-MM-DD&to=YYYY-MM-DD
             CSVダウンロードボタンの正体はただのリンク。
             → 設計書§5の「XHR直叩き」が成立。Playwrightでボタンを押す必要は無い。

  文字コード : UTF-8（BOM付き）→ decode は "utf-8-sig"

  CSVの中身 :
      日付,無人入店/解錠数,無人常連来店数,無人購買回数,無人購買率 (%)
      2026-03-20,612,91,0,0
    左3列が transform.normalize_entry() の期待どおり
    （日付→date / 無人入店/解錠数→来店客数 / 無人常連来店数→常連来店数）。

  期間 : 開店日〜前日を1リクエストで取得できることを実測で確認。
         山形124日 / いわき88日 / 福井39日 が欠けずに返った。

【ログイン方式】
  まず requests（ブラウザ無し）で試し、失敗したら Playwright に自動で切り替える。
  普段は数秒で終わり、サイトがJavaScriptログインに変わっても保険が効く。

【使い方】
  python fetch_digitail.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

import config
import db
import transform


# ============================================================
# 設定
# ============================================================
BASE_URL = "https://dashboard.digitail-tech.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

# 日本時間。GitHub Actions は UTC で動くので、必ず明示的にJSTへ直す。
# （これをやらないと「前日」が1日ズレる）
JST = timezone(timedelta(hours=9))

RAW_DIR = Path("raw") / "digitail"

# config.py の店舗名 → デジテールのURLに使う名前（スラッグ）
# ⚠️ config.py は変更禁止ファイルなので、対応表はここに置く。
#    新店舗を足すときは、ここに1行足す。
DIGITAIL_SLUGS = {
    "山形":   "notime_yamagata",
    "いわき": "notime_iwaki",
    "福井":   "notime_fukui",
    # 下北沢（notime_shimokitazawa）もデータ自体は存在するが、
    # config.py が has_entry_data=False のため対象外。触らない。
}

# CSVの左3列がこの名前であることを確認する。
# サイト側が列名を変えたら、黙って壊れずにここで気づけるようにする。
EXPECTED_HEADER = ["日付", "無人入店/解錠数", "無人常連来店数"]

RETRIES = 3            # 設計書§4-4
RETRY_WAIT = 2         # 秒。失敗するたび 2 → 4 → 8 と待つ
TIMEOUT = 30           # 秒


class DigitailError(Exception):
    """デジテール取得の失敗。分かりやすいメッセージを必ず添える。"""


# ============================================================
# 準備
# ============================================================
def load_env() -> tuple[str, str]:
    """
    ID / パスワードを読み込む。

    優先順位:
      1. すでに設定されている環境変数（GitHub Actions はこちら）
      2. 同じフォルダの .env ファイル（手元のPCはこちら）

    ⚠️ ID・パスワードをこのファイルに直接書いてはいけない。
    """
    env_file = Path(__file__).with_name(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # 環境変数が既にあればそちらを優先（Actionsの値を上書きしない）
            os.environ.setdefault(key, value)

    user = os.environ.get("DIGITAIL_ID", "")
    password = os.environ.get("DIGITAIL_PW", "")

    if not user or not password:
        raise DigitailError(
            "デジテールのIDまたはパスワードが見つかりません。\n"
            f"  → {env_file} を作り、次の2行を書いてください:\n"
            "       DIGITAIL_ID=あなたのユーザー名\n"
            "       DIGITAIL_PW=あなたのパスワード\n"
            "     （.env は .gitignore 済みなのでGitHubには上がりません）"
        )
    return user, password


def yesterday_jst() -> str:
    """
    前日（日本時間）を YYYY-MM-DD で返す。

    設計書§6-1: 当日のデータは未確定なので必ず捨てる。
    """
    return (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d")


def today_jst() -> str:
    """実行日（日本時間）。生CSVのファイル名に使う。"""
    return datetime.now(JST).strftime("%Y-%m-%d")


def target_stores() -> list:
    """
    取得対象の店舗を config.py から決める。

    「入店データがある」かつ「ソースがデジテール」の店舗だけ。
    下北沢は has_entry_data=False なので自動的に外れる。
    """
    stores = [s for s in config.stores_with_entry_data()
              if s.entry_source == "digitail"]

    unknown = [s.name for s in stores if s.name not in DIGITAIL_SLUGS]
    if unknown:
        raise DigitailError(
            f"config.py に登録されている店舗 {unknown} の、"
            "デジテール側の名前（スラッグ）が分かりません。\n"
            f"  → fetch_digitail.py の DIGITAIL_SLUGS に追加してください。\n"
            "     調べ方: デジテールでその店舗を選び、アドレス欄の\n"
            "     https://dashboard.digitail-tech.com/【ここ】/kpi/visits を見る。"
        )
    if not stores:
        raise DigitailError(
            "取得対象の店舗が1つもありません。"
            "config.py の has_entry_data / entry_source を確認してください。"
        )
    return stores


# ============================================================
# ログイン
# ============================================================
def _session_works(session: requests.Session) -> bool:
    """
    本当にログインできているかを、実際にCSVを1日分取って確かめる。

    ログインに失敗していると、CSVではなくログイン画面のHTMLが返ってくる。
    「HTTP 200が返った＝成功」と信じると、そこで気づけないので中身で判定する。
    """
    slug = next(iter(DIGITAIL_SLUGS.values()))
    day = yesterday_jst()
    try:
        text = _get_csv_text(session, slug, day, day)
    except Exception:
        return False
    return text.lstrip().startswith("日付")


def login_with_requests(user: str, password: str) -> requests.Session | None:
    """
    ブラウザを使わずにログインする（速い・軽い）。

    ログイン画面は普通のフォーム送信で、隠しトークンも無いことを確認済み。
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "notime-kpi-bot"})
    try:
        session.post(
            LOGIN_URL,
            data={"username": user, "password": password},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        print(f"  requests でのログインに失敗しました（通信エラー）: {e}")
        return None

    if _session_works(session):
        return session
    return None


def login_with_playwright(user: str, password: str) -> requests.Session:
    """
    保険の方式。実際にブラウザを動かしてログインし、
    そこで得たログイン情報を requests に引き継ぐ（設計書§5）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise DigitailError(
            "ブラウザ無しのログインに失敗し、Playwrightも入っていません。\n"
            "  → 次の2つを順番に実行してください:\n"
            "       pip install playwright\n"
            "       playwright install chromium"
        )

    print("  Playwright（ブラウザ操作）に切り替えます...")
    session = requests.Session()
    session.headers.update({"User-Agent": "notime-kpi-bot"})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL, timeout=TIMEOUT * 1000)

        # セレクタは意味依存で書く（設計書§5）。
        # ⚠️ id は «R35»-username のようにReactが毎回作り直すので絶対に使わない。
        page.fill('input[name="username"]', user)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=TIMEOUT * 1000)

        for c in context.cookies():
            session.cookies.set(c["name"], c["value"], domain=c.get("domain"))
        browser.close()

    if not _session_works(session):
        raise DigitailError(
            "デジテールにログインできませんでした。\n"
            "  よくある原因:\n"
            "    ・.env のユーザー名／パスワードが間違っている\n"
            "      （メールアドレスではなく「ユーザー名」です）\n"
            "    ・パスワードが変更された\n"
            "    ・アカウントがロックされている\n"
            "  → まずご自分のブラウザで "
            f"{LOGIN_URL} にログインできるか確かめてください。"
        )
    return session


def login(user: str, password: str) -> requests.Session:
    """まず軽い方式、ダメならブラウザ方式。"""
    print("デジテールにログインしています...")
    session = login_with_requests(user, password)
    if session is not None:
        print("  ログイン成功（ブラウザ無し）")
        return session

    print("  ブラウザ無しのログインが通りませんでした。")
    session = login_with_playwright(user, password)
    print("  ログイン成功（Playwright）")
    return session


# ============================================================
# CSV取得
# ============================================================
def _get_csv_text(session: requests.Session, slug: str,
                  start: str, end: str) -> str:
    """CSVを1回だけ取りに行く（リトライ無し）。文字コードはUTF-8 BOM付き。"""
    url = f"{BASE_URL}/{slug}/kpi/visits/download"
    params = {"interval": "day", "from": start, "to": end}
    resp = session.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content.decode("utf-8-sig")


def fetch_csv(session: requests.Session, store_name: str, slug: str,
              start: str, end: str) -> str:
    """
    CSVを取得する。失敗したら間隔を空けて3回まで試す（設計書§4-4）。
    """
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            text = _get_csv_text(session, slug, start, end)
            if not text.lstrip().startswith("日付"):
                raise DigitailError(
                    "CSVではないものが返ってきました"
                    "（ログインが切れた可能性があります）"
                )
            return text
        except Exception as e:
            last_error = e
            if attempt < RETRIES:
                wait = RETRY_WAIT * (2 ** (attempt - 1))
                print(f"    {attempt}回目失敗（{e}）。{wait}秒待って再試行します...")
                time.sleep(wait)

    raise DigitailError(
        f"【{store_name}】のCSVを{RETRIES}回試しても取得できませんでした。\n"
        f"  最後のエラー: {last_error}\n"
        f"  URL: {BASE_URL}/{slug}/kpi/visits/download"
        f"?interval=day&from={start}&to={end}"
    )


def save_raw(text: str, store_name: str) -> Path:
    """
    加工前の生CSVを保存する（設計書§4-1 / 最優先）。

    スクレイパーが壊れて数日気づかなくても、これがあれば後から復元できる。
    捨てるとその期間は永久に欠損する。
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{today_jst()}_{store_name}.csv"
    path.write_text(text, encoding="utf-8-sig")
    return path


# ============================================================
# 検証（書き込む前の自己チェック / 設計書§4-3）
# ============================================================
def check_csv(text: str, store_name: str, cutoff: str) -> pd.DataFrame:
    """
    おかしいデータでDBを上書きしてしまう事故を防ぐ。
    通らなければ、保存せずに分かりやすいエラーを出す。
    """
    df = pd.read_csv(StringIO(text))

    if len(df) == 0:
        raise DigitailError(
            f"【{store_name}】のCSVが空でした（見出し行しかありません）。\n"
            "  考えられる原因: 期間の指定が間違っている／その店舗にまだデータが無い。"
        )

    if len(df.columns) < 3:
        raise DigitailError(
            f"【{store_name}】のCSVの列が足りません（{len(df.columns)}列）。\n"
            f"  実際の見出し: {list(df.columns)}\n"
            "  デジテール側のCSVの形が変わった可能性があります。"
        )

    actual = list(df.columns[:3])
    if actual != EXPECTED_HEADER:
        raise DigitailError(
            f"【{store_name}】のCSVの見出しが想定と違います。\n"
            f"  想定: {EXPECTED_HEADER}\n"
            f"  実際: {actual}\n"
            "  デジテール側で列名が変わった可能性があります。\n"
            "  → 中身を確認して、fetch_digitail.py の EXPECTED_HEADER を直してください。\n"
            f"  生CSVは {RAW_DIR} に保存済みなので、データは失われていません。"
        )

    return df


# ============================================================
# メイン
# ============================================================
def run() -> int:
    user, password = load_env()
    stores = target_stores()
    cutoff = yesterday_jst()

    print("=" * 60)
    print("NOTIME 入店データ取得（デジテールストア）")
    print(f"  実行日     : {today_jst()}（日本時間）")
    print(f"  取得する最終日: {cutoff} ← 当日は数字が未確定なので取りません")
    print(f"  対象店舗   : {', '.join(s.name for s in stores)}")
    print("=" * 60)

    db.init_db()
    session = login(user, password)

    results = []
    failures = []

    for store in stores:
        slug = DIGITAIL_SLUGS[store.name]
        start = store.open_date
        print(f"\n【{store.name}】{start} 〜 {cutoff}")

        try:
            text = fetch_csv(session, store.name, slug, start, cutoff)

            # ① まず生CSVを保存（何があっても、これだけは先に残す）
            raw_path = save_raw(text, store.name)
            print(f"  生CSVを保存: {raw_path}")

            # ② 書き込む前に中身を検証
            df = check_csv(text, store.name, cutoff)
            print(f"  取得日数: {len(df)}日分")

            if cutoff not in df.iloc[:, 0].astype(str).values:
                # 致命的ではないので止めない。ただし黙らない（設計書§4-2）。
                print(f"  ⚠️ 注意: 前日({cutoff})のデータが入っていません。"
                      "デジテール側の反映待ちかもしれません。")

            # ③ 正規化 → DB保存
            entry = transform.normalize_entry(df)
            saved = db.import_entries(entry, store.name)
            print(f"  DBに保存: {saved}日分")

            results.append((store.name, len(df), saved,
                            int(entry["来店客数"].sum())))

        except DigitailError as e:
            print(f"  ❌ 失敗: {e}")
            failures.append(store.name)
        except Exception as e:
            print(f"  ❌ 想定外のエラー: {type(e).__name__}: {e}")
            failures.append(store.name)

    # ---- まとめ ----
    print("\n" + "=" * 60)
    print("結果")
    print("=" * 60)
    for name, rows, saved, visitors in results:
        print(f"  ✅ {name:<6} CSV {rows:>4}日分 → DB {saved:>4}日分 "
              f"／ 来店客数 合計 {visitors:,}人")
    for name in failures:
        print(f"  ❌ {name:<6} 取得できませんでした（上のメッセージを見てください）")

    if failures:
        print(f"\n⚠️ {len(failures)}店舗が失敗しました。"
              "成功した店舗のデータはDBに入っています。")
        return 1

    print(f"\n✅ 全{len(results)}店舗ぶんを notime.db に保存しました。")
    return 0


def main() -> int:
    try:
        return run()
    except DigitailError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n❌ 想定外のエラーで止まりました: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
