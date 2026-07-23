"""
NOTIME データベース層（SQLite）

【目的】
売上・来店データを「明細レベルの粒度のまま」貯めておき、
後から任意の期間・任意の切り口で分析できるようにする。

集計結果ではなく生データを保存するのがポイント。
集計はいつでも作り直せるが、捨てた明細は戻らない。

【保存する粒度】
  transactions      : 伝票（レシート）1件 = 1行。時刻つき
  transaction_lines : 商品明細 1行 = 1行。カテゴリ・単価・点数
  entries           : 入店データ（店舗×日）

【後からできる分析の例】
  - 任意の期間の売上・客単価・購買率
  - 時間帯別（何時が売れるか） ← ts があるので可能
  - 曜日別、月別、経過週別
  - カテゴリ別、価格帯別
  - 店舗間比較、レジ別比較
  - 「5月の土曜の15〜18時にアウターは何着売れたか」のような複合条件

【冪等性の担保】
POSは過去日のデータが後から変わる（未確定 → 確定で伝票数が増える）。
そのため取り込みは (store, pos_name, date) 単位で
「既存を削除してから挿入」する。何度取り込んでも正しい状態になる。
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

DB_PATH = Path("notime.db")

SCHEMA = """
-- 伝票（レシート）単位
CREATE TABLE IF NOT EXISTS transactions (
    tx_id         TEXT PRIMARY KEY,
    store         TEXT NOT NULL,
    pos_name      TEXT NOT NULL,
    date          TEXT NOT NULL,          -- YYYY-MM-DD
    ts            TEXT,                   -- YYYY-MM-DD HH:MM:SS（時間帯分析用）
    sales_ex_tax  REAL NOT NULL,
    sales_in_tax  REAL NOT NULL,
    qty           REAL NOT NULL,
    imported_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_store_date ON transactions(store, date);
CREATE INDEX IF NOT EXISTS idx_tx_date       ON transactions(date);

-- 商品明細
CREATE TABLE IF NOT EXISTS transaction_lines (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_id        TEXT NOT NULL,
    store        TEXT NOT NULL,
    pos_name     TEXT NOT NULL,
    date         TEXT NOT NULL,
    ts           TEXT,
    category     TEXT,                    -- 正規化後のカテゴリ名
    price        REAL,                    -- 税抜単価
    qty          REAL,
    amount       REAL,                    -- 明細金額（税抜）
    bundle_code  TEXT,
    is_parent    INTEGER,
    is_child     INTEGER,
    is_normal    INTEGER,
    imported_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_line_store_date ON transaction_lines(store, date);
CREATE INDEX IF NOT EXISTS idx_line_category   ON transaction_lines(category);
CREATE INDEX IF NOT EXISTS idx_line_tx         ON transaction_lines(tx_id);

-- 入店データ（店舗×日）
CREATE TABLE IF NOT EXISTS entries (
    store           TEXT NOT NULL,
    date            TEXT NOT NULL,
    visitors        INTEGER,
    repeat_visitors INTEGER,
    imported_at     TEXT NOT NULL,
    PRIMARY KEY (store, date)
);

-- 取り込み履歴（いつ何を取り込んだかの監査用）
CREATE TABLE IF NOT EXISTS import_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,      -- cashier / digitail / airregi / ezregi
    store       TEXT,
    date_from   TEXT,
    date_to     TEXT,
    row_count   INTEGER,
    imported_at TEXT NOT NULL
);

-- 日次KPIビュー（よく使う集計をSQLひとつで引けるように）
CREATE VIEW IF NOT EXISTS v_daily_kpi AS
SELECT
    t.store,
    t.date,
    COUNT(DISTINCT t.tx_id)                         AS buyers,
    SUM(t.qty)                                      AS items,
    SUM(t.sales_ex_tax)                             AS sales_ex_tax,
    SUM(t.sales_in_tax)                             AS sales_in_tax,
    e.visitors                                      AS visitors,
    CAST(COUNT(DISTINCT t.tx_id) AS REAL) / NULLIF(e.visitors, 0) AS buy_rate,
    SUM(t.sales_ex_tax) / NULLIF(COUNT(DISTINCT t.tx_id), 0)      AS avg_ticket,
    SUM(t.qty)        / NULLIF(COUNT(DISTINCT t.tx_id), 0)        AS avg_items,
    SUM(t.sales_ex_tax) / NULLIF(SUM(t.qty), 0)                   AS unit_price,
    SUM(t.sales_ex_tax) / NULLIF(e.visitors, 0)                   AS sales_per_visitor
FROM transactions t
LEFT JOIN entries e ON e.store = t.store AND e.date = t.date
GROUP BY t.store, t.date;
"""


@contextmanager
def connect(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH):
    """テーブルとビューを作成（既にあれば何もしない）。"""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


# ============================================================
# 取り込み
# ============================================================
def _now() -> str:
    return pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")


def import_pos(pos_df: pd.DataFrame, db_path: Path = DB_PATH) -> dict:
    """
    共通スキーマのPOSデータをDBに取り込む。

    ⚠️ (store, pos_name, date) 単位で「削除 → 挿入」する。
       POSは過去日の数字が後から変わる（未確定→確定）ため、
       単純なINSERTだと重複し、UPSERTだと取消された伝票が残る。
       パーティション丸ごと入れ替えるのが唯一正しい。
    """
    if len(pos_df) == 0:
        return {"transactions": 0, "lines": 0}

    now = _now()
    # 伝票単位（明細行にヘッダ値が繰り返されるので first）
    tx = (pos_df.groupby("tx_id")
          .agg(store=("store", "first"), pos_name=("pos_name", "first"),
               date=("date", "first"), ts=("ts", "first"),
               sales_ex_tax=("sales_ex_tax", "first"),
               sales_in_tax=("sales_in_tax", "first"),
               qty=("tx_qty", "first"))
          .reset_index())
    tx["imported_at"] = now

    lines = pd.DataFrame({
        "tx_id": pos_df["tx_id"],
        "store": pos_df["store"],
        "pos_name": pos_df["pos_name"],
        "date": pos_df["date"],
        "ts": pos_df["ts"],
        "category": pos_df["line_category"],
        "price": pos_df["line_price"],
        "qty": pos_df["line_qty"],
        "amount": pos_df["line_amount"],
        "bundle_code": pos_df["bundle_code"],
        "is_parent": pos_df["is_parent"].astype(int),
        "is_child": pos_df["is_child"].astype(int),
        "is_normal": pos_df["is_normal"].astype(int),
        "imported_at": now,
    })

    partitions = tx[["store", "pos_name", "date"]].drop_duplicates()

    with connect(db_path) as conn:
        for _, p in partitions.iterrows():
            conn.execute(
                "DELETE FROM transactions WHERE store=? AND pos_name=? AND date=?",
                (p["store"], p["pos_name"], p["date"]))
            conn.execute(
                "DELETE FROM transaction_lines WHERE store=? AND pos_name=? AND date=?",
                (p["store"], p["pos_name"], p["date"]))
        tx.to_sql("transactions", conn, if_exists="append", index=False)
        lines.to_sql("transaction_lines", conn, if_exists="append", index=False)
        conn.execute(
            "INSERT INTO import_log (source, store, date_from, date_to, row_count, imported_at)"
            " VALUES (?,?,?,?,?,?)",
            ("pos", ",".join(sorted(tx["store"].unique())),
             tx["date"].min(), tx["date"].max(), len(tx), now))

    return {"transactions": len(tx), "lines": len(lines)}


def import_entries(entry_df: pd.DataFrame, store: str, db_path: Path = DB_PATH) -> int:
    """
    入店データを取り込む。date が主キーなので UPSERT で上書き。
    （入店データは日単位の単一値なので、パーティション削除は不要）
    """
    if len(entry_df) == 0:
        return 0
    now = _now()
    rows = [(store, r["date"], int(r["来店客数"]), int(r["常連来店数"]), now)
            for _, r in entry_df.iterrows()]
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO entries (store, date, visitors, repeat_visitors, imported_at)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(store, date) DO UPDATE SET"
            "   visitors=excluded.visitors,"
            "   repeat_visitors=excluded.repeat_visitors,"
            "   imported_at=excluded.imported_at",
            rows)
        conn.execute(
            "INSERT INTO import_log (source, store, date_from, date_to, row_count, imported_at)"
            " VALUES (?,?,?,?,?,?)",
            ("digitail", store, entry_df["date"].min(), entry_df["date"].max(), len(rows), now))
    return len(rows)


# ============================================================
# 取り出し（分析用）
# ============================================================
def query(sql: str, params: tuple = (), db_path: Path = DB_PATH) -> pd.DataFrame:
    """任意のSQLを投げる。アドホック分析用の万能口。"""
    with connect(db_path) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def daily_kpi(start: str, end: str, stores: list[str] | None = None,
              db_path: Path = DB_PATH) -> pd.DataFrame:
    """期間を指定して日次KPIを取り出す。"""
    sql = "SELECT * FROM v_daily_kpi WHERE date BETWEEN ? AND ?"
    params = [start, end]
    if stores:
        sql += f" AND store IN ({','.join('?' * len(stores))})"
        params += stores
    return query(sql + " ORDER BY store, date", tuple(params), db_path)


def hourly(start: str, end: str, stores: list[str] | None = None,
           db_path: Path = DB_PATH) -> pd.DataFrame:
    """時間帯別。「何時に売れているか」を見る。"""
    sql = """
        SELECT store,
               CAST(strftime('%H', ts) AS INTEGER) AS hour,
               COUNT(*)             AS buyers,
               SUM(sales_ex_tax)    AS sales_ex_tax
        FROM transactions
        WHERE date BETWEEN ? AND ?
    """
    params = [start, end]
    if stores:
        sql += f" AND store IN ({','.join('?' * len(stores))})"
        params += stores
    return query(sql + " GROUP BY store, hour ORDER BY store, hour", tuple(params), db_path)


def by_category(start: str, end: str, stores: list[str] | None = None,
                exclude_non_merch: bool = True, db_path: Path = DB_PATH) -> pd.DataFrame:
    """カテゴリ別の売上・販売数。"""
    sql = """
        SELECT store, category,
               SUM(amount) AS sales_ex_tax,
               SUM(qty)    AS items
        FROM transaction_lines
        WHERE date BETWEEN ? AND ?
    """
    params = [start, end]
    if exclude_non_merch:
        sql += " AND category NOT IN ('レジ袋','クーポン','不明')"
    if stores:
        sql += f" AND store IN ({','.join('?' * len(stores))})"
        params += stores
    return query(sql + " GROUP BY store, category ORDER BY store, sales_ex_tax DESC",
                 tuple(params), db_path)


def by_price_band(start: str, end: str, stores: list[str] | None = None,
                  db_path: Path = DB_PATH) -> pd.DataFrame:
    """価格帯別。実商品ライン（通常＋バンドル子）のみ。"""
    sql = """
        SELECT store,
               CASE
                 WHEN price <= 1980 THEN '〜¥1,980'
                 WHEN price <= 3980 THEN '¥2,980〜3,980'
                 WHEN price <= 5980 THEN '¥4,490〜5,980'
                 WHEN price <= 9980 THEN '¥6,980〜9,980'
                 ELSE '¥11,980〜'
               END AS band,
               SUM(amount) AS sales_ex_tax,
               SUM(qty)    AS items
        FROM transaction_lines
        WHERE date BETWEEN ? AND ?
          AND (is_normal = 1 OR is_child = 1)
          AND category NOT IN ('レジ袋','クーポン','不明')
    """
    params = [start, end]
    if stores:
        sql += f" AND store IN ({','.join('?' * len(stores))})"
        params += stores
    return query(sql + " GROUP BY store, band", tuple(params), db_path)


def coverage(db_path: Path = DB_PATH) -> pd.DataFrame:
    """
    どの店舗の、どの期間のデータが入っているか。
    「いつからいつまで分析できるか」を確認する用。
    """
    return query("""
        SELECT store,
               MIN(date) AS first_date,
               MAX(date) AS last_date,
               COUNT(DISTINCT date) AS days,
               COUNT(*)             AS transactions,
               SUM(sales_ex_tax)    AS sales_ex_tax
        FROM transactions
        GROUP BY store ORDER BY store
    """, db_path=db_path)
