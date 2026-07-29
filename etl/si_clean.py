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

# SIPOSファイル上の名前を、既存の“直営店”の名前に付け替える対応表。
# 下北沢は直営店（Airレジ＋SIPOSの2レジ）。SIPOS分は直営「下北沢」の売上として合算する。
# ここでは旧名「下北沢」に寄せる（この後 get_or_create_store の STORE_NAME_ALIAS が
# 正式名「NOTIME下北沢店」へそろえる／既存店をリネームする）。
SI_STORE_ALIAS = {
    "古着屋NOTIME下北沢店": "下北沢",
}

# 直営店の“正式名”（DB上の店名。FCへ書き換えない保護に使う）。
DIRECT_STORE_NAMES = {"NOTIME山形店", "NOTIMEいわき店", "NOTIME福井店", "NOTIME下北沢店"}
# 直営店の“地名キーワード”（直営っぽい店名の取りこぼし検知に使う）。
DIRECT_LOCATION_KEYWORDS = {"山形", "いわき", "福井", "下北沢"}


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


def direct_lookalikes(final_names) -> list[str]:
    """
    “直営店の地名を含むのに、直営店として畳まれていない店名”を返す（取りこぼし検知）。

    例: 直営「下北沢」を SIPOS の別名で畳み忘れると、"○○下北沢店" が新規FC店として
    作られてしまう。そういう危険な名前を洗い出して、取り込み時に警告するためのもの。
    SIPOS＝FCとは限らない（直営もSIPOSを使う）ので、この検知で人が気づけるようにする。
    """
    safe = DIRECT_LOCATION_KEYWORDS | set(SI_STORE_ALIAS) | DIRECT_STORE_NAMES
    out = []
    for name in sorted(set(str(n) for n in final_names)):
        if name in safe:
            continue
        if any(kw in name for kw in DIRECT_LOCATION_KEYWORDS):
            out.append(name)
    return out


def si_store_names(name_series: pd.Series) -> pd.Series:
    """
    生の店舗名（ブランド名は残す）を最終的な店名にそろえる。
    ・前後の空白を除去。
    ・SI_REBRAND に載っている“単純な改名”だけ最新名へ置き換える。
    """
    s = name_series.astype(str).str.strip()
    return s.replace(SI_REBRAND).replace(SI_STORE_ALIAS)
