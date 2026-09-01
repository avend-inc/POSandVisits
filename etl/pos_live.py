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
        return rows_mod.airregi_common(df, pos_name)   # CSVの店舗名で振り分け
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
                 if c["pos_type"] in ("airregi", "ezregi", "cashier")]
    except Exception as e:
        print(f"  接続一覧の取得に失敗: {e}")
        return [Result("pos_connections", "failed", str(e))]

    if not conns:
        print("  レジ接続（Air/EZ）が未登録です。管理画面『レジ接続』で登録してください。")
        return []

    # SIPOS(ezregi)は購買情報明細が「テナント全店ぶん」を返し、店舗名で振り分ける。
    # 同一テナント（同一ホスト）に複数接続があると同じ全店CSVを何度も落として時間の無駄
    # （＝日次のタイムアウト要因）になるので、ezregi はホスト単位で1接続に集約する。
    # airregi は1接続=1店（全店CSVではない）ため集約しない。
    from urllib.parse import urlparse as _urlparse

    def _host(u: str) -> str:
        u = (u or "").strip()
        if "://" not in u:
            u = "https://" + u
        return (_urlparse(u).netloc or "").lower()

    # PW復号済みの接続を優先して残す（同一ホストで最初の1件が未復号だと取得できないため）。
    ez_by_host: dict[str, dict] = {}
    others: list = []
    order: list = []
    for c in conns:
        if c["pos_type"] != "ezregi":
            others.append(c)
            order.append(("other", len(others) - 1))
            continue
        h = _host(c.get("url")) or f"__id{c['id']}"
        prev = ez_by_host.get(h)
        if prev is None:
            ez_by_host[h] = c
            order.append(("ez", h))
        elif not prev.get("login_pw") and c.get("login_pw"):
            ez_by_host[h] = c  # PW復号できる方に差し替え
    deduped: list = []
    for kind, key in order:
        deduped.append(others[key] if kind == "other" else ez_by_host[key])
    if len(deduped) != len(conns):
        print(f"  SIPOSテナント集約: {len(conns)}接続 → {len(deduped)}（同一ホストは1回のみ取得）")
    conns = deduped

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
        # ingest_log の source（＝二重取り込み判定キー）は接続ごとに一意にする。
        #  account単位の接続（store_id=null）が複数あり pos_name も同じだと、
        #  (source, 営業日, store_id) が衝突し、先に成功した1本を見て残りが
        #  「取り込み済み」とスキップされる（所沢が毎日抜けた原因）。接続idを付けて必ず分ける。
        src = f'pos_{c["pos_name"] or c["pos_type"]}#{c["id"]}'
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
            if c["pos_type"] == "cashier":
                # 直営(Secretアカウント)以外の cashier アカウント（例: 天王台）を、接続の
                # url/id/pw で直営と同じ手順（ログイン→検索→CSV出力(明細)）で取得する。
                from . import cashier_fetch
                # cashierは全アカウント同じサイト。接続URLがトップ等でも確実に取引一覧を
                # 開けるよう、正規の取引一覧URL(既定)を使う（trade_urlは渡さない）。
                csv_text = cashier_fetch.fetch_range_creds(
                    c["login_id"] or "", c["login_pw"] or "",
                    business_date, business_date, headless=headless)
            else:
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
            # cashier は「レジ名(pos_name)」を必ず "cashier" に固定する。
            #  重複排除は (store_id, pos_name, tx_id) で行うため、backfill(parse_cashier_csv
            #  ＝"cashier")や直営Secret経路と pos_name を揃えないと、同じ伝票が接続の表示名で
            #  別レジ扱いになり二重計上される（所沢の二重計上の原因）。cashierは店をCSVの
            #  店舗名で振り分けるので、接続の表示名は保存キーに使わない。
            _pos_name = "cashier" if c["pos_type"] == "cashier" else (c["pos_name"] or c["pos_type"])
            common = _to_common(csv_text, c["pos_type"], _pos_name)
            common = common[common["date"].notna()].copy()
            if len(common) == 0:
                msg = f"{business_date} の {label} は明細0件（休業日等ならこれで正常）。"
                print(f"  ℹ️ {msg}")
                sb.log(run_id=run_id, source=src, business_date=business_date,
                       store_id=c.get("store_id"), status="no_data", message=msg, started_at=started)
                results.append(Result(label, "no_data", msg))
                continue
            common["line_no"] = common.groupby("tx_id").cumcount()

            # 店舗の対応付け【設計：接続＝オーナー単位アカウント】
            #  cashier・SIPOS・Airレジ 等いずれも URL/ID/PW は「オーナー単位」で、その
            #  オーナーが持つ全店が1アカウントに出てくる。よって取り込みは【常にCSVの
            #  「店舗名」で振り分ける】。接続の store_id には固定しない
            #  （＝1店に固定すると、そのアカウントの全店売上が1店に丸ごと化ける。
            #    長野に山形＋いわき＋福井が入った事故がこれ）。
            #  store_pos.store_id は取り込みを支配しない（参考情報／空でよい）。
            #  店名は歴史データと同じ命名（ブランド名を残し、略称は STORE_NAME_ALIAS で
            #  正式名へ寄せる）にそろえて既存 store_id に一致させる。
            csv_stores = [s for s in common["store"].astype(str).str.strip().unique() if s]
            store_id_of = lambda name: sb.get_or_create_store(name, cache)

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
