"""
設定と共通の道具箱

【このファイルの役割】
  ・ID/パスワードなどを「環境変数」から読む（ファイルには絶対に書かない）
  ・日本時間の「前日」を計算する
  ・URL や待ち時間などの定数を1か所にまとめる

【重要】
  ID・パスワード・Supabaseのキーは、
    - GitHub で動かすとき → GitHub の「Secrets」
    - 自分のPCで動かすとき → このフォルダの .env ファイル（.gitignore 済み）
  から読み込みます。ソースコードに直接書いてはいけません。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ------------------------------------------------------------
# 基本
# ------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent

# 日本時間。GitHub Actions は UTC で動くので、必ず明示的にJSTへ直す。
# （これをやらないと「前日」が1日ズレます）
JST = timezone(timedelta(hours=9))

RAW_DIR = ROOT / "raw"          # 加工前のCSVを置く場所（.gitignore済み）
DEBUG_DIR = ROOT / "debug"      # 失敗時のスクリーンショット等（.gitignore済み）

# ------------------------------------------------------------
# 取得先
# ------------------------------------------------------------
CASHIER_TRADE_URL = "https://cashier.jp/v2/client/trade"
CASHIER_LOGIN_URL = "https://login.cashier.jp/login/"

DIGITEL_BASE_URL = "https://dashboard.digitail-tech.com"
DIGITEL_LOGIN_URL = f"{DIGITEL_BASE_URL}/auth/login"

# デジテールの来店数CSVの見出し（左3列）。
# サイト側が列名を変えたら、黙って壊れずにここで気づけるようにする。
DIGITEL_EXPECTED_HEADER = ["日付", "無人入店/解錠数", "無人常連来店数"]

# cashier 明細CSVの必須列（adapters.adapt_cashier が使う列）
CASHIER_REQUIRED_COLUMNS = [
    "伝票：精算日付（締日）", "伝票：処理日時", "伝票：取引番号", "伝票：店舗名",
    "伝票：小計金額", "伝票：合計金額", "伝票：購入点数",
    "明細：商品カテゴリ名", "明細：販売価格", "明細：商品点数", "明細：商品小計金額",
    "明細：商品バンドルコード", "明細：商品コード１",
]

# ブラウザ操作のタイムアウト（ミリ秒）とリトライ
BROWSER_TIMEOUT_MS = 60_000
RETRIES = 3
RETRY_WAIT_SEC = 5

# Supabase へ1回に送る行数（大きすぎるとタイムアウトする）
SUPABASE_CHUNK = 500


class EtlError(Exception):
    """ETLの失敗。必ず「何が起きて、どうすればいいか」を日本語で添える。"""


# ------------------------------------------------------------
# 環境変数（.env → 環境変数）
# ------------------------------------------------------------
def load_dotenv() -> None:
    """
    手元のPCで動かすとき用に .env を読み込む。

    すでに環境変数がある場合はそちらを優先する
    （GitHub Actions の Secrets を .env で上書きしないため）。
    """
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str, hint: str) -> str:
    """必須の環境変数を取り出す。無ければ、直し方まで書いたエラーを出す。"""
    value = os.environ.get(name, "").strip()
    if not value:
        raise EtlError(
            f"設定 {name} が見つかりません。({hint})\n"
            "  ・GitHubで動かす場合  : リポジトリの Settings → Secrets and variables →\n"
            f"                          Actions → New repository secret で {name} を登録\n"
            f"  ・自分のPCで動かす場合: このフォルダの .env に {name}=... の行を追加\n"
            "  （.env は .gitignore 済みなのでGitHubには上がりません）"
        )
    return value


# ------------------------------------------------------------
# 日付
# ------------------------------------------------------------
def today_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def yesterday_jst() -> str:
    """
    前日（日本時間）を YYYY-MM-DD で返す。

    当日のデータはまだ確定していないので、必ず前日を取り込む。
    """
    return (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d")


def validate_date(text: str) -> str:
    """YYYY-MM-DD の形かどうか確かめる。"""
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise EtlError(f"日付の書き方が違います: {text!r}（正しい例: 2026-07-22）")


def new_run_id() -> str:
    """1回の実行につき1つのID。ingest_log で同じ実行の行をまとめて見られる。"""
    return datetime.now(JST).strftime("%Y%m%d-%H%M%S")
