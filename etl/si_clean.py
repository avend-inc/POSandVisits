"""
Si/EZレジ(SIPOS)CSVの取り込み前クリーニング

SIPOSの書き出しは、実データ上つぎのクセがある（2026-07 実ファイルで確認）:
  ・購入日時の書式がゆれる（"2024/12/01 0:02:04" と "2025/11/8 2:17" が混在）
    → pandas が最初の行の形で決め打ちして、秒なしの行を落としてしまう（0.4%）。
  ・店舗名のブランド接頭辞がバラバラ（NOTIME○○店 / SELFURUGI○○店 /
    SELFURUGI GARAGE○○ / 古着屋NOTIME○○店）。同じ店が別名で出ることがある。

adapters.py は「変更禁止ファイル」（他システムと共有）なので、
ここで **アダプタに渡す前後** に補正する。

【店の識別方針（ユーザー確定 2026-07-29）】
  ・表示名は **ブランド名を残す**（生の店舗名をそのまま使う）。
  ・同一店の単純な改名（同じ場所でブランドだけ変わった）だけ、最新名に寄せる。
      - 倉敷店: 「NOTIME倉敷店」→「SELFURUGI倉敷店」（同一コード8000000の改名）。
  ・コード6900002 は「NOTIME早稲田店」→「SELFURUGI GARAGE鎌ヶ谷」と“場所ごと”変わって
    いるため、名前で自然に別店として分ける（生の名前ベースにすれば自動でそうなる）。
"""
from __future__ import annotations

import pandas as pd

# 同一店の“単純な改名”を最新名へ寄せる対応表（場所は同じ・ブランドだけ変わったもの）。
# ※ 場所ごと変わったもの（例: 早稲田→鎌ヶ谷）は入れない＝別店として分ける。
SI_REBRAND = {
    "NOTIME倉敷店": "SELFURUGI倉敷店",
}


def normalize_si_datetime(df: pd.DataFrame, col: str = "購入日時") -> pd.DataFrame:
    """
    購入日時の書式ゆれ（秒あり/なし・ゼロ埋め有無）を吸収して、
    アダプタが1つの形で読めるように "YYYY-MM-DD HH:MM:SS" 文字列へそろえる。
    読めない値だけ空にする（＝その行は後段で自然に除外される）。
    """
    if col not in df.columns:
        return df
    df = df.copy()
    dt = pd.to_datetime(df[col], errors="coerce", format="mixed")
    df[col] = dt.dt.strftime("%Y-%m-%d %H:%M:%S")
    return df


def si_store_names(name_series: pd.Series) -> pd.Series:
    """
    生の店舗名（ブランド名は残す）を最終的な店名にそろえる。
    ・前後の空白を除去。
    ・SI_REBRAND に載っている“単純な改名”だけ最新名へ置き換える。
    """
    s = name_series.astype(str).str.strip()
    return s.replace(SI_REBRAND)
