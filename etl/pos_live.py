"""
レジ自動取得（ライブ）— store_pos の接続でログインし、当日ぶんの売上を取り込む

日次ETL(run_daily)から呼ばれる。cashier は従来どおり別処理。ここは Airレジ／EZレジ 等、
アプリの「レジ接続」に登録された接続を巡回して sales に入れる。

  ・接続ごとに URL/ID/PW（PWはVaultから復号）でログイン → CSV取得 → 共通スキーマ変換 → sales。
  ・店舗の対応付け:
      - 接続に store_id がある（下北沢のAir/EZ等・1アカウント=1店）→ その店に固定。
      - store_id が無い（1アカウントで複数店＝Si等）→ CSVの店舗名で自動振り分け。
  ・重複日は取り込まない（ingest_log。--force で解除）。何が起きたか必ず記録。
"""
from __future__ import annotations

import io
import traceback
from datetime import datetime

import pandas as pd

import adapters
from .settings import JST, EtlError
from .pos_sources import load_connections
from . import pos_web, rows as rows_mod


SI_NEEDED = ["店舗コード", "店舗名", "レジNo", "レシートNo", "購入日時",
             "商品分類名", "売上数", "本体価格", "税抜売価", "税込売価"]


def _now() -> str:
    return datetime.now(JST).isoformat()


def _read_si_table(text: str) -> pd.DataFrame:
    """【Si】DB/EZレジのCSV：集計行を飛ばして「店舗コード…」見出しから読む。"""
    lines = text.splitlines()
    hdr = next((i for i, ln in enumerate(lines)
                if "店舗コード" in ln and "レシートNo" in ln), None)
    if hdr is None:
        raise EtlError("CSVに見出し行（店舗コード…レシートNo…）が見つかりませんでした。")
    df = pd.read_csv(io.StringIO("\n".join(lines[hdr:])), dtype=str,
                     engine="python", on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _to_common(csv_text: str, pos_type: str, pos_name: str) -> pd.DataFrame:
    if pos_type == "ezregi":
        # SIPOSの正は「売上報告書→購買情報明細（purchaseDetailsList）」＝Si明細と同形式
        # （店舗コード/商品分類名/税込売価…）。カテゴリ別も出せ、売上・客数・点数が
        # 売上報告書に一致する。旧「取引照会CSV（取引日付…1取引1行・明細なし）」は
        # 保険として見出しで判定して振り分ける（通常は明細側を通る）。
        head = (csv_text or "")[:2000]
        if "取引日付" in head and ("合計金額" in head or "レシートNo" in head and "合計点数" in head):
            df = pd.read_csv(io.StringIO(csv_text), dtype=str)
            return adapters.adapt_ezregi_tran(df, pos_name)
        # 購買情報明細（Si明細形式）。店名は生の店舗名ベース（ブランド名を残す）で
        # そろえる＝手動インポート(import_pos_csv)と完全に同じにして store_id を一致させる。
        from . import si_clean
        df_in = _read_si_table(csv_text)
        df_in = si_clean.normalize_si_datetime(df_in)
        common = adapters.adapt_ezregi(df_in, pos_name)
        # adapt_ezregi と同じ規則（fillna('')後に店舗名が空でない行）で残した行から店名を
        # 作り、位置対応で割り当てる（reindexのラベルずれによるNaN混入・誤店舗割当を防ぐ）。
        kept = df_in[df_in["店舗名"].fillna("").astype(str).str.strip() != ""]
        names = si_clean.si_store_names(kept["店舗名"]).astype(str)
        if len(names) == len(common):
            common["store"] = names.to_numpy()
        else:
            common["store"] = si_clean.si_store_names(df_in["店舗名"]).reindex(common.index)
        common = common[common["store"].notna()].copy()
        common["store"] = common["store"].astype(str)
        common = common[~common["store"].isin(["", "nan", "None"])].copy()
        return common
    if pos_type == "airregi":
        df = pd.read_csv(io.StringIO(csv_text), dtype=str)
        return adapters.adapt_airregi(df, pos_name, pos_name)
    # cashier フォーマットもCSV取り込み可能に（保険）
    df = pd.read_csv(io.StringIO(csv_text), dtype=str)
    return adapters.adapt_cashier(df, pos_name)


class Result:
    def __init__(self, name, status, message="", inserted=0, duplicate=0):
        self.name, self.status, self.message = name, status, message
        self.inserted, self.duplicate = inserted, duplicate


def run_live_pos(sb, business_date: str, run_id: str,
                 force: bool = False, headless: bool = True) -> list[Result]:
    print("\n" + "=" * 60)
    print(f"【3】レジ自動取得（Air/EZ等の接続）  対象営業日: {business_date}")
    print("=" * 60)

    try:
        conns = [c for c in load_connections(sb, only_active=True)
                 if c["pos_type"] in ("airregi", "ezregi")]
    except Exception as e:
        print(f"  接続一覧の取得に失敗: {e}")
        return [Result("pos_connections", "failed", str(e))]

    if not conns:
        print("  レジ接続（Air/EZ）が未登録です。管理画面『レジ接続』で登録してください。")
        return []

    # 接続の状態サマリ（PWは中身を出さず有無だけ）。復号できたPWが0なら設定漏れ/RPC未作成。
    import os as _os0
    n_pw_ok = sum(1 for c in conns if c.get("login_pw"))
    n_secret = sum(1 for c in conns if c.get("pw_secret_id"))
    print(f"  接続一覧（active/Air・EZ）: {len(conns)}件 / Vault秘密あり {n_secret}件 / PW復号OK {n_pw_ok}件")
    if n_pw_ok == 0 and n_secret > 0:
        print("  ⚠️ Vaultに秘密はあるがPWを1件も復号できません。"
              "get_pos_secret 未作成の可能性 → sql/011_pos_fetch.sql を実行してください。")
    # 明細は動作確認（POS_LIMIT / POS_ONLY_STORE / POS_DEBUG）のときだけ。日次は静かに。
    if (_os0.environ.get("POS_LIMIT") or _os0.environ.get("POS_ONLY_STORE")
            or _os0.environ.get("POS_DEBUG")):
        for c in conns:
            print(f"    #{c['id']:>3} {c['pos_type']:<8} name={ (c.get('pos_name') or '') !r:<10} "
                  f"store_id={c.get('store_id')} url={'○' if c.get('url') else '×'} "
                  f"id={'○' if c.get('login_id') else '×'} "
                  f"secret={'○' if c.get('pw_secret_id') else '×'} "
                  f"pw(復号)={'○' if c.get('login_pw') else '×'}")

    # 動作確認用：POS_LIMIT=1 や POS_ONLY_STORE=店名 で対象を絞る（目印確定のテスト向け）。
    import os as _os
    only_store = (_os.environ.get("POS_ONLY_STORE") or "").strip()
    if only_store:
        conns = [c for c in conns if only_store in (c.get("pos_name") or "")
                 or str(c.get("store_id")) == only_store]
    limit = (_os.environ.get("POS_LIMIT") or "").strip()
    if limit.isdigit() and int(limit) > 0:
        # テストでは「実際にログインできる接続（URL・ID・PWが揃っている）」を優先。
        # id順の先頭に PW未設定の古い接続があると流れ確認にならないため。
        usable = [c for c in conns if c.get("url") and c.get("login_id") and c.get("login_pw")]
        conns = (usable or conns)[:int(limit)]
        print(f"  （テスト用に対象を {len(conns)} 件へ絞りました：POS_LIMIT={limit}）")
        for c in conns:
            print(f"    → 対象: {c['pos_type']}#{c['id']} pos_name={c.get('pos_name')!r} "
                  f"store_id={c.get('store_id')} url={(c.get('url') or '')[:60]}")

    cache = sb.store_map()
    results: list[Result] = []
    for c in conns:
        label = f'{c["pos_type"]}#{c["id"]}'
        src = f'pos_{c["pos_name"] or c["pos_type"]}'
        started = _now()

        # 二重取り込みチェック（store_id基準。複数店アカウントはstore_id無し=全体で1本）
        if not force and sb.already_succeeded(src, business_date, c.get("store_id")):
            msg = f"{business_date} の {label} は取り込み済み（--force で再取り込み）。"
            print(f"  ⛔ {msg}")
            sb.log(run_id=run_id, source=src, business_date=business_date,
                   store_id=c.get("store_id"), status="rejected_duplicate",
                   message=msg, started_at=started)
            results.append(Result(label, "rejected_duplicate", msg))
            continue

        if not c.get("login_pw"):
            msg = f"{label}: パスワード未設定（管理画面『レジ接続』で登録してください）。"
            print(f"  ⚠️ {msg}")
            sb.log(run_id=run_id, source=src, business_date=business_date,
                   store_id=c.get("store_id"), status="failed", message=msg, started_at=started)
            results.append(Result(label, "failed", msg))
            continue

        try:
            csv_text = pos_web.fetch(c["url"], c["login_id"], c["login_pw"],
                                     business_date, c["pos_type"], label=label, headless=headless)
            import os as _os2
            if _os2.environ.get("POS_DEBUG") or _os2.environ.get("POS_ONLY_STORE"):
                head = "\n".join((csv_text or "").splitlines()[:8])
                print(f"  [CSV先頭 {label}] 長さ={len(csv_text or '')}\n----\n{head}\n----")
            if not (csv_text or "").strip():
                msg = f"{business_date} の {label} は明細0件（休業日・売上なしならこれで正常）。"
                print(f"  ℹ️ {msg}")
                sb.log(run_id=run_id, source=src, business_date=business_date,
                       store_id=c.get("store_id"), status="no_data", message=msg, started_at=started)
                results.append(Result(label, "no_data", msg))
                continue
            common = _to_common(csv_text, c["pos_type"], c["pos_name"] or c["pos_type"])
            common = common[common["date"].notna()].copy()
            if len(common) == 0:
                msg = f"{business_date} の {label} は明細0件（休業日等ならこれで正常）。"
                print(f"  ℹ️ {msg}")
                sb.log(run_id=run_id, source=src, business_date=business_date,
                       store_id=c.get("store_id"), status="no_data", message=msg, started_at=started)
                results.append(Result(label, "no_data", msg))
                continue
            common["line_no"] = common.groupby("tx_id").cumcount()

            # 店舗の対応付け：
            #  ・接続に store_id あり（1アカウント=1店）→ その店に固定。
            #    ただしCSVに複数店舗が混在していたら“共通アカウント”なので警告する
            #    （店舗分けが必要。store_pos.store_id を空にすると下の名前振り分けになる）。
            #  ・store_id 無し（1アカウントで複数店＝Si等）→ 店舗名で振り分け。
            #    その際は歴史データと同じ命名（ブランド名を残し、改名だけ最新へ）にそろえる。
            csv_stores = [s for s in common["store"].astype(str).str.strip().unique() if s]
            if c.get("store_id"):
                if len(csv_stores) > 1:
                    print(f"  ⚠️ {label}: 1アカウントに複数店舗（{', '.join(csv_stores[:10])}）。"
                          "今は接続の店舗にまとめて記録します（正しく分けるには接続のstore_idを空にしてください）。")
                fixed = int(c["store_id"])
                store_id_of = lambda name, _sid=fixed: _sid       # 接続=1店に固定
            else:
                if c["pos_type"] == "ezregi":
                    from .si_clean import si_store_names
                    common["store"] = si_store_names(common["store"])
                store_id_of = lambda name: sb.get_or_create_store(name, cache)  # CSV店舗名で振り分け

            payload = rows_mod.cashier_rows(common, store_id_of)
            inserted, duplicate = sb.insert_ignore_duplicates(
                "sales", payload, on_conflict="store_id,pos_name,tx_id,line_no")
            stores = sorted(common["store"].astype(str).unique().tolist())
            print(f"  {label}: 保存 新規 {inserted}行 / 無視 {duplicate}行（店舗: {', '.join(stores[:10])}）")
            sb.log(run_id=run_id, source=src, business_date=business_date,
                   store_id=c.get("store_id"), status="success",
                   rows_fetched=len(payload), rows_inserted=inserted, rows_duplicate=duplicate,
                   message=f"店舗: {', '.join(stores[:20])}", started_at=started)
            results.append(Result(label, "success", "", inserted, duplicate))
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            print(f"  ❌ {label} 失敗: {detail}")
            if not isinstance(e, EtlError):
                traceback.print_exc()
            sb.log(run_id=run_id, source=src, business_date=business_date,
                   store_id=c.get("store_id"), status="failed", message=detail[:2000], started_at=started)
            results.append(Result(label, "failed", detail))

    return results
