"""
NOTIME 店舗・POS構成の設定

【拡張のしかた】
新しい店舗を足すときは、このファイルの STORES に1エントリ追加するだけ。
transform.py などのロジック側は一切触らない。

- 1店舗に複数POSを持てる（例: 下北沢 = Airレジ + EZレジ）
- POSごとにフォーマット（アダプタ）を指定
- 入店データが無い店舗も許容（has_entry_data=False）
"""
from dataclasses import dataclass, field


@dataclass
class PosConfig:
    """1つのPOS（レジ）の設定。1店舗に複数持てる。"""
    name: str            # 識別名（例: "AirREGI", "EZREGI", "cashier"）
    adapter: str         # CSVフォーマットのアダプタ名 → adapters.py の ADAPTERS のキー
    # そのPOSのCSV内で、この店舗を指す店舗名（表記ゆれは正規化で吸収）
    store_label: str | None = None


@dataclass
class StoreConfig:
    """1店舗の設定。"""
    name: str                          # 表示名（例: "下北沢"）
    open_date: str                     # 開店日 YYYY-MM-DD（経過週の起点）
    pos_list: list[PosConfig]          # この店舗のPOS（複数可）
    has_entry_data: bool = True        # 入店データ（来店客数）が取れるか
    entry_source: str | None = "digitail"   # 入店データのソース
    # 店舗固有のカテゴリ読み替え（例: 福井の「Tシャツ」タグは実際はラグ）
    category_override: dict[str, str] = field(default_factory=dict)


# ============================================================
# 店舗一覧 — ここに足すだけで新店舗が追加できる
# ============================================================
STORES: list[StoreConfig] = [
    StoreConfig(
        name="山形",
        open_date="2026-03-20",
        pos_list=[
            PosConfig(name="cashier", adapter="cashier", store_label="NOTIME山形"),
        ],
        has_entry_data=True,
        entry_source="digitail",
    ),
    StoreConfig(
        name="いわき",
        open_date="2026-04-25",
        pos_list=[
            PosConfig(name="cashier", adapter="cashier", store_label="NOTIMEいわき"),
        ],
        has_entry_data=True,
        entry_source="digitail",
    ),
    StoreConfig(
        name="福井",
        open_date="2026-06-13",
        pos_list=[
            PosConfig(name="cashier", adapter="cashier", store_label="NOTIME福井"),
        ],
        has_entry_data=True,
        entry_source="digitail",
        # 福井のみ「Tシャツ」タグは実際にはラグ
        category_override={"Tシャツ": "ラグ"},
    ),
    StoreConfig(
        name="下北沢",
        open_date="2026-01-01",   # TODO: 実際の開店日に修正
        pos_list=[
            PosConfig(name="AirREGI", adapter="airregi", store_label="下北沢"),
            PosConfig(name="EZREGI",  adapter="ezregi",  store_label="下北沢"),
        ],
        # 入店カウンターが無いので来店客数が取れない
        # → 購買率・来店単価は算出不能（Noneになる）。売上・客単価・商品単価は出せる。
        has_entry_data=False,
        entry_source=None,
    ),
]

STORE_MAP = {s.name: s for s in STORES}


def get_store(name: str) -> StoreConfig:
    return STORE_MAP[name]


def stores_with_entry_data() -> list[StoreConfig]:
    """入店データがある店舗のみ（購買率などの比較用）"""
    return [s for s in STORES if s.has_entry_data]


# ============================================================
# カテゴリ定義（全店共通）
# ============================================================
CATEGORIES = [
    "Tシャツ", "プリントTシャツ", "バンド/ミュージックTシャツ", "アニメ/ミュージックTシャツ",
    "アウター/ジャケット", "長袖シャツ", "半袖シャツ", "ポロシャツ",
    "スウェット/パーカー", "セーター/フリース", "デニム", "チノ/スラックス等",
]
# 物販（商品分析）から除外するカテゴリ
#  - レジ袋 / クーポン: 商品ではない
#  - 不明: EZレジは商品明細が無いため、カテゴリ・価格帯分析には使えない
#          （売上・購入客数・客単価には正しく含まれる）
NON_MERCH = ["レジ袋", "クーポン", "不明"]

PRICE_BANDS = [
    (0, 1980, "〜¥1,980"),
    (1981, 3980, "¥2,980〜3,980"),
    (3981, 5980, "¥4,490〜5,980"),
    (5981, 9980, "¥6,980〜9,980"),
    (9981, 10**9, "¥11,980〜"),
]

WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]
