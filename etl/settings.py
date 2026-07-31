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
import re
import unicodedata
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

# 直営店の「正式名（表示名）」。取り込み時、店舗名をこの正式名へそろえる。
#   店名は“表示名”であると同時に“取り込みで店を見分けるカギ”でもある。
#   直営店をブランド付きの正式名に統一しても、従来の名前（cashierの「山形」等）が
#   正しく同じ店にマッチし続けるよう、ここで正式名へ変換する。
#   （旧名のまま既存している場合は、get_or_create_store が正式名へ自動リネームする）
#
#   ⚠️ ここに書くのは「ブランド名が付かない略称」など、名前の正規化だけでは
#      同じ店だと分からないものだけ。「NOTIME天王台店」と「NOTIME天王台」の
#      ような “店/スペース の有無” の違いは STORE_KEY で自動的に吸収される。
STORE_NAME_ALIAS = {
    "山形":   "NOTIME山形店",
    "いわき": "NOTIMEいわき店",
    "福井":   "NOTIME福井店",
    "下北沢": "NOTIME下北沢店",
}


def _clean_name(name: str) -> str:
    """
    店舗名の表記ゆれをそろえる下ごしらえ。
      ・全角/半角のゆれ（NFKC。全角英数→半角、全角スペース→半角 など）
      ・前後・途中の空白（半角/全角）を全部除去
    """
    n = unicodedata.normalize("NFKC", name or "")
    n = re.sub(r"\s+", "", n)
    return n.strip()


def store_key(name: str) -> str:
    """
    「同じ店か？」を判定するためのカギ（正規化キー）を作る。

    システム（POS / デジテール / cashier）ごとに店舗名の書き方が違っても、
    同じ店なら同じカギになるようにする。具体的には:
      ① 空白と全角/半角のゆれを消す（_clean_name）
      ② 略称→正式名の対応（STORE_NAME_ALIAS）を当てる（例: 山形→NOTIME山形店）
      ③ 末尾の「店」を落とす（例: NOTIME天王台店 と NOTIME天王台 を同じ扱いに）

    例:
      "NOTIME天王台店" → "NOTIME天王台"
      "NOTIME天王台"   → "NOTIME天王台"
      "ＮＯＴＩＭＥ 天王台 店" → "NOTIME天王台"
      "山形"           → "NOTIME山形"（①→②で NOTIME山形店 → ③で 店 を落とす）
    """
    n = _clean_name(name)
    n = STORE_NAME_ALIAS.get(n, n)   # 略称→正式名（このキーも空白なしで持つ）
    n = _clean_name(n)
    if n.endswith("店"):
        n = n[:-1]
    return n


def canonical_store_name(name: str) -> str:
    """
    新しく店舗を登録するときの「表示名」を決める。
    略称なら正式名へ直す（例: 山形 → NOTIME山形店）。それ以外は空白だけ整えて返す。
    """
    n = _clean_name(name)
    return STORE_NAME_ALIAS.get(n, n)


def better_store_name(a: str, b: str) -> str:
    """
    2つの店舗名のうち、表示名としてふさわしい方を選ぶ。
    重複店をまとめるときに「どちらの名前を残すか」を決めるのに使う。
    優先順位: ブランド名で始まる > 末尾が「店」 > 余計な空白が無い > 文字数が多い。
    """
    def score(x: str) -> tuple:
        x = (x or "").strip()
        brand = 1 if (x.startswith("NOTIME") or x.startswith("SELFURUGI")) else 0
        has_ten = 1 if x.endswith("店") else 0
        no_space = 0 if re.search(r"\s", x) else 1
        return (brand, has_ten, no_space, len(re.sub(r"\s+", "", x)))
    return a if score(a) >= score(b) else b

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
