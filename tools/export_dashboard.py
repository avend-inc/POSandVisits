"""
ダッシュボード用データの集計と配信

Supabase の sales / visits を集計して data.json を作り、
ダッシュボードHTML(index.html) と一緒に Supabase Storage へアップロードする。

  python -m tools.export_dashboard              # 集計 → data.json/index.html を配信
  python -m tools.export_dashboard --print-json # data.json を標準出力にも出す

【2つのモード（環境変数 SUPABASE_ANON_KEY の有無で自動切替）】
  ● 公開モード（SUPABASE_ANON_KEY なし＝従来）
      公開バケット 'dashboard' に index.html と data.json を置く。
      URLを知っていれば誰でも見られる。
        {SUPABASE_URL}/storage/v1/object/public/dashboard/index.html
  ● 認証モード（SUPABASE_ANON_KEY あり）
      index.html は公開バケット 'dashboard'（URLは変わらない）。
      data.json は非公開バケット 'dashboard-data' に置き、
      Googleログイン（社内 @avend.co.jp のみ）した人だけが読める。
      HTMLにはログインに必要な公開情報(URL/anonキー)を埋め込む。
      ※ anonキーは公開して問題ない鍵。実データはRLSで保護される。
      公開バケット側の古い data.json は削除して漏れを防ぐ。

※ data.json は「日別×店舗」の素の合計だけを持ち、週次(月〜日)/月次への集約や
   比率(購入率・客単価など)の計算は、画面側(dashboard.html)で行う。
   ＝比率の平均という誤りを避け、どの粒度でも正しく出す。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.settings import JST, EtlError, load_dotenv  # noqa: E402
from etl.supabase_client import Supabase  # noqa: E402

# ダッシュボードHTMLは GitHub Pages が配信する（Supabaseはブラウザ向けHTMLを
# text/plain でしか返せず「ソース表示」になるため）。Supabase には data.json だけ
# 置き、GitHub Pages のページがそれを読み込む。data.json は fet().json() で読むので
# Content-Type は問題にならない。日次ETLはこの 'dashboard' バケットを更新する。
BUCKET_PUBLIC = "dashboard"        # data.json（＋互換のため index.html も置く）
BUCKET_PRIVATE = "dashboard-data"  # 認証モードの data.json（RLSで保護）
PAGE = 1000
# クリエイティブ別（広告単位）を何日ぶん載せるか。全期間だと data.json が太るわりに、
# 入れ替わりの早いクリエイティブを古いぶんまで比べても打ち手にならない。
META_AD_DAYS = int(os.environ.get("META_AD_DAYS") or 180)


_COLUMNS_CACHE: dict = {}


def _columns(sb: Supabase, table: str) -> set:
    """そのテーブルに実在する列名を PostgREST のスキーマから取る。

    「取り込みの拡張で増えた列を、無い環境でも落ちずに拾う」ために使う。
    以前は select を欲張った順に投げて、400が返ったら諦める作りだったが、
    **1つでも存在しない列を混ぜると、その組み合わせごと落ちる**。
    実際に video_avg_seconds（削除済みの旧列名）が1つ混じっていたせいで、
    同じ組に入っていた likes / profile_visits まで丸ごと落ちて、
    画面が「未取込」表示のままになっていた。存在する列だけを選べばこれは起きない。
    """
    if not _COLUMNS_CACHE:
        try:
            resp = sb._send("GET", f"{sb.url}/rest/v1/")
            _COLUMNS_CACHE["spec"] = resp.json() if resp.status_code == 200 else {}
        except Exception:
            _COLUMNS_CACHE["spec"] = {}
    defs = (_COLUMNS_CACHE.get("spec") or {}).get("definitions") or {}
    return set(((defs.get(table) or {}).get("properties") or {}))


def _select_cols(sb: Supabase, table: str, base: str, optional: list) -> str:
    """必須の列＋実在する任意列だけを繋いだ select 文字列を作る。

    スキーマが取れなかったときは base だけで進む（欠けても集計は止まらない）。
    """
    have = _columns(sb, table)
    if not have:
        return base
    picked = [c for c in optional if c in have]
    missing = [c for c in optional if c not in have]
    if missing:
        print(f"  （{table} に無い列は読み飛ばします: {', '.join(missing)}）")
    return base + ("," + ",".join(picked) if picked else "")


def _select_all(sb: Supabase, table: str, select: str,
                order: str, extra: dict | None = None) -> list[dict]:
    """PostgREST のページ制限(1000件)を超えて全件取る。"""
    rows: list[dict] = []
    offset = 0
    while True:
        params = {"select": select, "order": order,
                  "limit": str(PAGE), "offset": str(offset)}
        if extra:
            params.update(extra)
        chunk = sb.select(table, params)
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        offset += PAGE
    return rows


def build_data(sb: Supabase) -> dict:
    # "*" で全列取得。ownership 列が未追加でも動くよう .get で既定「直営」にする。
    # id が NULL の行（＝共有storesに在庫アプリだけが持つ店。私たちのPOS対象外で
    # 売上も無い）は、ダッシュボードの店舗リスト・選択肢に混ざらないよう除外する。
    stores = [s for s in sb.select("stores", {"select": "*", "order": "id"})
              if s.get("id") is not None]
    name_by_id = {s["id"]: s["name"] for s in stores}
    # 直営/FC。空欄を「直営」で埋めない。埋めると、区分が未設定なだけの店が
    # 直営として集計され、広告の直営ページにFC店が出る（実際に起きた）。
    # 未設定は未設定のまま渡して、画面側で「区分が未設定」と分かるようにする。
    own_by_id = {s["id"]: (s.get("ownership") or None) for s in stores}
    # KPIに来店数を使うか（列が未追加でも既定 True）。False の店は来店・購入率をKPIから外す。
    kv_by_id = {s["id"]: bool(s.get("kpi_visitors", True)) for s in stores}
    # ダッシュボードに表示するか（列が未追加でも既定 True）。False の店は各画面の一覧・合計から外す。
    vis_by_id = {s["id"]: bool(s.get("visible", True)) for s in stores}

    # --- sales: 伝票(税込/税抜/点数)は明細行に繰り返し入っているので、
    #     伝票単位では tx_id ごとに1回だけ数える。明細(金額/点数)は行ごとに合計。
    #     ※ pos_name も取得：1店舗に複数レジ(下北沢=Airレジ+EZレジ)がある場合、
    #        別レジで伝票番号が偶然かぶっても別取引として数える（合算漏れ防止）。
    sales = _select_all(
        sb, "sales",
        "business_date,store_id,pos_name,tx_id,sales_in_tax,sales_ex_tax,tx_qty,"
        "line_category,line_amount,line_qty,bundle_code,is_parent",
        order="id",
    )

    # --- レジの営業形態（無人／有人）。下北沢のように1店で2レジを使い分ける店で、
    #     売上を「無人時間（SIPOS＝EZレジ）」と「有人時間（Airレジ）」に分けるために使う。
    #     判定はまず store_pos の pos_type（登録済みの接続情報）を正とし、
    #     未登録のレジ名は名前から推定する（履歴データや手動取り込みぶんの保険）。
    pos_type_by_name: dict[tuple, str] = {}   # (store_id, pos_name) -> pos_type
    try:
        for r in sb.select("store_pos", {"select": "store_id,pos_type,pos_name"}):
            pn = (r.get("pos_name") or "").strip()
            if pn:
                pos_type_by_name[(r["store_id"], pn)] = r.get("pos_type") or ""
    except Exception:
        pass

    def _pos_mode(store_id, pos_name: str) -> str:
        """そのレジが無人営業(u)か有人営業(m)か。判別できなければ有人(m)扱い。"""
        pn = (pos_name or "").strip()
        t = pos_type_by_name.get((store_id, pn)) or ""
        if t == "ezregi":
            return "u"
        if t in ("airregi", "cashier"):
            return "m"
        u = pn.upper()
        if u.startswith("AIR"):          # AirREGI＝有人（スタッフがレジを打つ）
            return "m"
        if u in ("SIPOS", "SI", "EZREGI") or "SIPOS" in u or u.startswith("EZ"):
            return "u"                   # SIPOS/EZレジ＝無人営業時間のセルフレジ
        if u == "DIGITEL":               # デジテール日次売上＝無人店の売上
            return "u"
        return "m"

    # バンドル名（コード→名称）。bundle_master が未作成でも動く。
    bundle_names: dict[str, str] = {}
    try:
        for b in sb.select("bundle_master", {"select": "code,name"}):
            if b.get("code"):
                bundle_names[str(b["code"])] = b.get("name") or str(b["code"])
    except Exception:
        pass

    # カテゴリ→季節区分（SS/AW/other）と、グレード別 価格帯（税抜）。未作成でも動く。
    cat_season: dict[str, str] = {}
    grade_bands: list[dict] = []
    try:
        for r in sb.select("category_season", {"select": "category,season"}):
            if r.get("category"):
                cat_season[str(r["category"])] = r.get("season") or "other"
    except Exception:
        pass
    try:
        for r in sb.select("grade_bands", {"select": "*", "order": "season,sort"}):
            grade_bands.append({"season": r["season"], "grade": r["grade"],
                                "lo": r["lo"], "hi": r.get("hi"),
                                "ntm": r.get("ntm_pct"), "sfg": r.get("sfg_pct"),
                                "sort": r.get("sort", 0)})
    except Exception:
        pass

    # 進行中の「当日(JST)」は“途中”なのでダッシュボードには出さない（締日＝最後の
    # 完全な日）。データ自体はDBに残す：翌朝の通常ETLが当日ぶんを「前日＝完全な日」
    # として取り込み、翌日以降このカットオフを自然に通過して表示される。
    # （誰かが当日を backfill しても、途中の数字を確定日と誤認させない安全弁）
    _today_jst = datetime.now(JST).date().isoformat()

    tx_seen: set[tuple] = set()
    daily: dict[tuple, dict] = {}     # (date, store_id) -> 伝票合計
    cat: dict[tuple, dict] = {}       # (date, store_id, category) -> 明細合計
    catprice: dict[tuple, dict] = {}  # (date, store_id, category, 単価) -> 明細合計（価格帯別）
    bundles: dict[tuple, dict] = {}   # (date, store_id, bundle_code) -> {tx集合, 親行の売上}

    # 商品ではないので「売上・点数・カテゴリ」すべてから除外する。
    #  ・レジ袋 / クーポン … 物ではない（config.py の NON_MERCH と同じ考え方）
    #  ・「不明」は EZレジで明細が無いだけの“実売上”なので売上には残す（除外しない）
    EXCLUDE = {"レジ袋", "クーポン"}
    # 「商品販売数(it)」には数えないが、売上・カテゴリ別には残す区分（ユーザー要望）。
    #  小物は“商品ではあるが点数に数えたくない”ため、点数だけから除外する。
    QTY_EXCLUDE = {"小物"}
    # 「（明細なし）」= SIPOS取引照会のように商品明細が無い実売上。
    #  売上・客数・点数などのKPIには含めるが、カテゴリ別/価格帯別ランキングには入れない
    #  （カテゴリ不明のものが「その他」として上位を占めてしまうのを防ぐ）。
    NOCAT = {"（明細なし）", "明細なし"}

    def _new() -> dict:
        # in/ex=伝票の税込/税抜合計、tx=伝票数、it=販売点数(レジ袋・クーポン・小物を除く)、
        # bag_in/bag_ex=レジ袋等の税込/税抜ぶん（KPIからは除外し、別枠で表示する）、
        # bag_q=レジ袋・クーポンの点数
        # ex=税抜の「実額」合計（明細/伝票に税抜がある店）。
        # ex_est=税抜が元データに無く 税込÷1.1 で“近似”した分（デジテールの日次合計のみの店）。
        #   → 実額が1円も無く近似だけの店日は、税抜KPIを「—」にする（推定値を出さない）。
        # ex_u/ex_m=税抜売上の営業形態別内訳（u=無人営業時間・m=有人営業時間）。
        #   2レジ運用の店（下北沢＝SIPOS＋Airレジ）の内訳表示に使う。
        # v_staffed=有人営業時間の来店数（画面から手入力したぶん。visits.source='staffed'）
        # kom_ex=小物の税抜売上。商品単価(税抜)の分子から差し引く用（分母の点数からも
        #   小物を除いているので、分子・分母を揃える）。客単価には小物を残す（差し引かない）。
        return {"in": 0.0, "ex": 0.0, "ex_est": 0.0, "tx": 0, "it": 0.0, "v": None,
                "ex_u": 0.0, "ex_m": 0.0, "v_staffed": None,
                "bag_in": 0.0, "bag_ex": 0.0, "bag_q": 0.0, "reji_in": 0.0, "reji_ex": 0.0,
                "coup_in": 0.0, "coup_q": 0.0,   # クーポン割引額・点数（レジ袋と分離）
                "kom_ex": 0.0}                   # 小物の税抜売上（商品単価の分子から除く用）

    def _num(v):
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    for r in sales:
        d = r["business_date"]
        if str(d) >= _today_jst:   # 進行中の当日は締めない
            continue
        sid = r["store_id"]
        dk = (d, sid)
        rec = daily.setdefault(dk, _new())

        ex_tax = _num(r["sales_ex_tax"])
        in_tax = _num(r["sales_in_tax"])

        # 伝票単位の値（税込/税抜合計・伝票数）は 伝票 ごとに1回だけ。
        # レジ(pos_name)を含めて識別（複数レジ店の番号衝突で取引が消えるのを防ぐ）。
        txkey = (sid, r.get("pos_name") or "", r["tx_id"])
        if txkey not in tx_seen:
            tx_seen.add(txkey)
            rec["in"] += in_tax
            # 税抜が“実額”か“近似(÷1.1)”かを分ける。
            # デジテール日次合計（pos=DIGITEL かつ 明細なし）は税抜が元データに無く近似。
            lc0 = (r.get("line_category") or "").strip()
            is_est_ex = (r.get("pos_name") == "DIGITEL") and (lc0 in NOCAT)
            if is_est_ex:
                rec["ex_est"] += ex_tax
            else:
                rec["ex"] += ex_tax
            # 税抜売上を「無人営業／有人営業」に振り分ける（レジ単位で判定）。
            rec["ex_u" if _pos_mode(sid, r.get("pos_name")) == "u" else "ex_m"] += ex_tax
            rec["tx"] += 1
            # 点数は tx_qty ではなく明細(line_qty)の合計で数える（下で加算）。
            # こうするとレジ袋・クーポンを点数から自然に除ける。

        c = (r.get("line_category") or "その他").strip() or "その他"
        amt = _num(r["line_amount"])
        qty = _num(r["line_qty"])

        # バンドル（セット割＝SALE）の集計：利用数=そのバンドルを使った伝票数、
        # 売上=親行(セール商品)の金額合計。名称は bundle_master から後で付ける。
        bcode = (r.get("bundle_code") or "").strip()
        if bcode:
            b = bundles.setdefault((d, sid, bcode), {"tx": set(), "a": 0.0})
            b["tx"].add(r["tx_id"])
            if r.get("is_parent"):
                b["a"] += amt

        if c in EXCLUDE:
            # レジ袋・クーポンぶんを控えておき、売上から差し引く。
            # 点数にもカテゴリにも入れない。
            rec["bag_ex"] += amt
            ratio = (in_tax / ex_tax) if ex_tax else 1.0   # 伝票の税率で税込換算
            rec["bag_in"] += amt * ratio
            rec["bag_q"] += qty
            if c == "レジ袋":                 # レジ袋売上は単体で（クーポン割引と混ぜない）
                rec["reji_in"] += amt * ratio
                rec["reji_ex"] += amt         # レジ袋の税抜（客単価・商品単価から差し引く用）
            if c == "クーポン":               # クーポン割引額・利用数を分離して記録
                rec["coup_in"] += amt * ratio
                rec["coup_q"] += qty
            continue

        # 販売点数（＝商品販売数）。レジ袋・クーポンは上で除外済み。さらに：
        #  ・小物 は点数に数えない（ユーザー要望：商品ではあるが点数から除く）。
        #  ・セット割の親行（is_parent＝「セール商品」）は点数に数えない。
        #    セット割は Airレジのジャーナルで「セール商品」という“セットの器”1行
        #    (is_parent・金額あり)＋中身の各商品行(is_child・金額0) の形で出る。
        #    実際に売れた商品は子行(is_child)として別に計上されるので、親行も数えると
        #    1セットぶんを二重計上してしまう（＝POSの商品販売数より過大になる原因）。
        #    → 親行は点数から除外し、実商品(子行)＋通常明細だけを数える。
        #    ※ 売上・カテゴリ別集計は従来どおり（親行の金額＝セット売上はそのまま残す）。
        if c not in QTY_EXCLUDE and not r.get("is_parent"):
            rec["it"] += qty

        # 小物の税抜売上を控える（商品単価の分子から差し引く用）。商品単価は分母(点数)から
        # 小物を除いているので、分子(売上)からも除いて分子・分母を揃える。売上KPI・
        # カテゴリ別・客単価には小物を残す（差し引かない）。
        if c == "小物":
            rec["kom_ex"] += amt

        # 明細なし（SIPOS取引照会）は 点数・売上KPI には含めるが、
        # カテゴリ別・価格帯別（→ランク帯）ランキングには入れない。
        if c in NOCAT:
            continue

        ck = (d, sid, c)
        crec = cat.setdefault(ck, {"a": 0.0, "q": 0.0})
        crec["a"] += amt
        crec["q"] += qty

        # 価格帯別（カテゴリ内の単価ごと）。単価＝明細金額÷点数を最寄りの円に丸める。
        if qty > 0 and amt > 0:
            price = int(round(amt / qty))
            pk = (d, sid, c, price)
            prec = catprice.setdefault(pk, {"a": 0.0, "q": 0.0})
            prec["a"] += amt
            prec["q"] += qty

    # --- visits: 来店客数を日別×店舗に載せる
    #   source='digitel' … 入店計測の自動取得（無人営業時間ぶん）
    #   source='staffed' … 有人営業時間ぶんの手入力（店舗ページから登録。sql/021）
    visits = _select_all(
        sb, "visits",
        "business_date,store_id,source,visitors",
        order="id", extra={"source": "in.(digitel,staffed)"},
    )
    for r in visits:
        if str(r["business_date"]) >= _today_jst:   # 進行中の当日は締めない
            continue
        dk = (r["business_date"], r["store_id"])
        rec = daily.setdefault(dk, _new())
        key = "v_staffed" if r.get("source") == "staffed" else "v"
        rec[key] = (rec[key] or 0) + int(r["visitors"] or 0)

    # --- 無人／有人の内訳を出す店（＝1店で両方のレジを使っている店。下北沢など）。
    #     1レジだけの店に内訳を持たせても意味が無いので、data.json にも載せない。
    mix_u: set = set()
    mix_m: set = set()
    for (_d, _sid), v in daily.items():
        if v["ex_u"] > 0:
            mix_u.add(_sid)
        if v["ex_m"] > 0:
            mix_m.add(_sid)
    mixed_stores = mix_u & mix_m

    def _extra(sid, v) -> dict:
        """日別行に足す任意項目。該当しない店・日には付けない（data.jsonを太らせない）。

          exU/exM … 税抜売上の内訳（無人営業／有人営業）。2レジ運用の店だけ。
          vm      … 有人営業時間の来店数（手入力ぶん）。入力がある日だけ。
        """
        out: dict = {}
        if sid in mixed_stores and not (v["ex"] <= 0 and v["ex_est"] > 0):
            out["exU"] = round(v["ex_u"])
            out["exM"] = round(v["ex_m"])
        if v["v_staffed"] is not None:
            out["vm"] = v["v_staffed"]
        return out

    daily_rows = [
        {"d": d, "s": sid,
         # 税込/税抜売上は「伝票合計そのまま」＝POSの純売上（レジ袋・小物・クーポン後の
         # 実売上を含む）。ロイヤリティ計算に使うため、POSの純売上と1円まで一致させる。
         # （レジ袋・クーポンの内訳は下の bag/coup で別途参照できる）
         "in": round(v["in"]),
         # デジテール日次合計のみの店日（明細＝税抜・点数が無い）は税抜・点数系KPIを null に。
         # → ダッシュボードで税抜売上・商品単価(税抜)・平均購入数・販売数は「—」表示。
         #   税込売上・取引数・客単価(税込)・来店・購入率は実額のまま。
         "ex": (None if (v["ex"] <= 0 and v["ex_est"] > 0) else round(v["ex"])),
         "tx": v["tx"],
         "it": (None if (v["ex"] <= 0 and v["ex_est"] > 0) else round(v["it"])),
         "v": v["v"],
         "bag": round(v["reji_in"]), "bagEx": round(v["reji_ex"]), "bagq": round(v["bag_q"]),
         # komEx=小物の税抜売上。商品単価(税抜)の分子から差し引く（分母の点数も小物を
         #   除いているため）。客単価は小物を残すので差し引かない。
         "komEx": round(v["kom_ex"]),
         # クーポン：利用数(点数)と割引額（値引はマイナス金額で入ることが多い）
         "coup": round(v["coup_q"]), "coupAmt": round(v["coup_in"]),
         # 無人／有人の売上内訳（exU/exM）と、有人来店の手入力（vm）。該当する店・日だけ付く。
         **_extra(sid, v)}
        for (d, sid), v in sorted(daily.items())
    ]
    # バンドル(SALE)別・日次：利用数(n=伝票数) と 売上(a=親行合計)
    bundle_rows = [
        {"d": d, "s": sid, "code": code, "n": len(v["tx"]), "a": round(v["a"])}
        for (d, sid, code), v in sorted(bundles.items())
    ]
    cat_rows = [
        {"d": d, "s": sid, "c": c, "a": round(v["a"]), "q": round(v["q"])}
        for (d, sid, c), v in sorted(cat.items())
    ]
    catprice_rows = [
        {"d": d, "s": sid, "c": c, "p": p, "a": round(v["a"]), "q": round(v["q"])}
        for (d, sid, c, p), v in sorted(catprice.items())
    ]

    # --- 診断: 店舗×月の税込売上(純売上) サマリー（ロイヤリティ照合用） ---
    #   POSの純売上と1円まで一致すべき。直近4か月ぶんを stdout に出す。
    try:
        msum: dict[tuple, float] = {}
        for r in daily_rows:
            k = (r["s"], r["d"][:7])
            msum[k] = msum.get(k, 0.0) + r["in"]
        months = sorted({m for (_, m) in msum})[-4:]
        if months:
            print("\n=== 店舗別 税込売上(純売上) 直近{}か月（ロイヤリティ照合用）===".format(len(months)))
            print("  店舗 | " + " | ".join(months))
            for s in stores:
                sid = s["id"]
                vals = [msum.get((sid, m), 0.0) for m in months]
                if any(v for v in vals):
                    nm = name_by_id.get(sid, str(sid))
                    print("  " + nm + " | " + " | ".join("{:,}".format(int(round(v))) for v in vals))
    except Exception as _e:
        print("  （売上サマリーの出力に失敗: {}）".format(_e))

    # --- 診断2: SIPOS系店舗の「店舗×月×pos_name」内訳（二重計上チェック＆照合用） ---
    #   ねらい：同じ店・同じ月に pos=SIPOS と pos=Si が両方あると二重計上になる。
    #   作り直し後は各(店,月)が1つのposだけになり、純売上が売上報告書に一致するはず。
    try:
        POS_KEEP = {"SIPOS", "Si"}
        agg: dict[tuple, dict] = {}      # (sid, ym, pos) -> {in, tx}
        seen_tx = set()
        for r in sales:
            pn = r.get("pos_name") or ""
            if pn not in POS_KEEP:
                continue
            ym = str(r["business_date"])[:7]
            key = (r["store_id"], ym, pn)
            a = agg.setdefault(key, {"in": 0.0, "tx": set()})
            txk = (r["store_id"], pn, r["tx_id"])
            if txk not in seen_tx:
                seen_tx.add(txk)
                a["in"] += _num(r["sales_in_tax"])
                a["tx"].add(r["tx_id"])
        # 店舗×月ごとに、存在するposを並べる（複数なら⚠️二重計上の疑い）。
        by_sm: dict[tuple, list] = {}
        for (sid, ym, pn), a in agg.items():
            by_sm.setdefault((sid, ym), []).append((pn, a))
        recent = sorted({ym for (_, ym) in by_sm})[-4:]
        print("\n=== 診断: SIPOS系 店舗×月×pos 内訳（直近{}か月・照合用）===".format(len(recent)))
        for (sid, ym) in sorted(by_sm, key=lambda k: (name_by_id.get(k[0], ""), k[1])):
            if ym not in recent:
                continue
            parts = by_sm[(sid, ym)]
            warn = " ⚠️二重計上の疑い(pos複数)" if len(parts) > 1 else ""
            detail = " / ".join("pos={} 純売上{:,}/伝票{}件".format(
                pn, int(round(a["in"])), len(a["tx"])) for pn, a in sorted(parts))
            print("  {} {} : {}{}".format(name_by_id.get(sid, sid), ym, detail, warn))
    except Exception as _e:
        print("  （SIPOS内訳の出力に失敗: {}）".format(_e))

    # --- Meta広告（日 × キャンペーン）---------------------------------------
    #   destination_id（在庫アプリの納品先＝店舗）を店名に直して持たせる。
    #   ここに入れた広告データは data.json（AVENDメンバーだけが読める）にだけ載る。
    #   加盟店へ配る store-<id>.json は別に組み立てているので、広告費は渡らない。
    #   ※ 比率（CTR/CPC/CPM/フリークエンシー）は入れない。期間をまたいで足すときに
    #     「比率の平均」という誤りが起きるため、画面側で 実額÷実額 で毎回出す。
    meta_rows: list[dict] = []
    meta_sync: dict | None = None
    try:
        dest_name: dict = {}
        try:
            for d in _select_all(sb, "destinations", "id,name", order="id"):
                dest_name[str(d["id"])] = d.get("name")
        except Exception:
            pass
        # 直営／FCの区分。納品先(destinations)の名前で当てにいくと表記ゆれで外すので、
        # pos_store_links（納品先 ↔ POS店舗の対応表）を通して stores.ownership を引く。
        # 同じ対応表から「納品先 → POS店舗ID」も作る。広告費と売上・来店を突き合わせる
        # ときは、店名ではなくこのIDで結ぶ（店名は表記ゆれがあり、当てにできない）。
        dest_own: dict = {}
        dest_sid: dict = {}
        try:
            for lk in _select_all(sb, "pos_store_links", "pos_store_id,destination_id", order="pos_store_id"):
                did, sid = lk.get("destination_id"), lk.get("pos_store_id")
                if did is not None and sid in own_by_id:
                    dest_own[str(did)] = own_by_id[sid]
                    dest_sid[str(did)] = sid
        except Exception:
            pass
        # 画面から直した「キャンペーン → 店舗」の割り当て（sql/025）。
        # 取り込みが書いた destination_id の上からかぶせる。取り直しても消えない。
        camp_override: dict = {}
        try:
            for o in _select_all(sb, "meta_campaign_stores",
                                 "campaign_id,destination_id", order="campaign_id"):
                cid = o.get("campaign_id")
                if cid:
                    camp_override[str(cid)] = o.get("destination_id")
        except Exception:
            pass    # まだテーブルが無い環境でも動く
        # follows / profile_visits / likes / video_avg_time_watched は取り込みの拡張後に
        # 増える列。実在する列だけを選ぶので、まだ無い環境でも落ちない。
        #
        # 平均視聴時間の列名は video_avg_time_watched。以前ここに書いてあった
        # video_avg_seconds は中身が入らないまま 2026-08-13 に削除された旧列で、
        # 存在しない列を混ぜたせいで同じ組の likes / profile_visits ごと
        # 取得に失敗していた（画面に「未取込」と出ていた原因）。
        BASE = ("date,account_name,campaign_id,campaign_name,destination_id,"
                "spend,impressions,reach,clicks")
        # landing_page_views は CPC の分母に要る。プロフアクセス数は最適化目標が
        # PROFILE_VISIT のキャンペーンでしか取れず、リンク遷移のキャンペーンでは
        # 分母が空のまま消化額だけが分子に乗る（福井でCPCが15円→66円に跳ねた原因）。
        # video_plays は平均視聴時間を再生数で重み付けするために持つ。
        OPTIONAL = ["follows", "profile_visits", "likes",
                    "video_avg_time_watched", "video_plays", "landing_page_views"]
        meta_src = _select_all(sb, "meta_insights_daily",
                               _select_cols(sb, "meta_insights_daily", BASE, OPTIONAL),
                               order="date")
        for r in meta_src:
            cid = r.get("campaign_id")
            did = r.get("destination_id")
            # 画面で割り当て直したキャンペーンは、そちらを優先する
            if cid is not None and str(cid) in camp_override:
                did = camp_override[str(cid)]
            meta_rows.append({
                "d": str(r["date"]),
                "a": r.get("account_name") or "(不明)",
                # ci=キャンペーンID。割り当て画面の書き込み先を決めるのに使う
                "ci": str(cid) if cid is not None else None,
                "c": r.get("campaign_name") or "(名称なし)",
                # st=店舗名。紐付いていないものは null のまま（画面で「未紐付け」と出す）
                "st": dest_name.get(str(did)) if did else None,
                # si=POS店舗ID。売上・来店と突き合わせるときの結び目
                "si": dest_sid.get(str(did)) if did else None,
                # ow=直営/FC。分からなければ null（画面では「区分なし」にまとめる）
                "ow": dest_own.get(str(did)) if did else None,
                "sp": round(_num(r.get("spend"))),
                "im": int(r.get("impressions") or 0),
                "rc": int(r.get("reach") or 0),
                "ck": int(r.get("clicks") or 0),
            })
            # フォロー数・プロフアクセス数・いいね数・平均視聴時間は、
            # 列がある環境だけ載せる（無い日は付けない＝画面では「—」になる）
            if r.get("follows") is not None:
                meta_rows[-1]["fl"] = int(r["follows"] or 0)
            if r.get("profile_visits") is not None:
                meta_rows[-1]["pv"] = int(r["profile_visits"] or 0)
            if r.get("likes") is not None:
                meta_rows[-1]["lk"] = int(r["likes"] or 0)
            # 平均視聴時間は「1再生あたりの秒数」。行をまたいで足せない値なので、
            # 画面側は再生数(vp)で重み付けして平均する。vp が無い行は単純平均に落とす。
            if r.get("video_avg_time_watched") is not None:
                meta_rows[-1]["vt"] = float(r["video_avg_time_watched"] or 0)
            if r.get("video_plays") is not None:
                meta_rows[-1]["vp"] = int(r["video_plays"] or 0)
            # lp=リンク遷移数。CPCの分母を pv+lp にするために持つ
            if r.get("landing_page_views") is not None:
                meta_rows[-1]["lp"] = int(r["landing_page_views"] or 0)
        runs = sb.select("meta_sync_runs", {
            "select": "started_at,status,unmapped_campaigns,rows_upserted,since,until",
            "order": "started_at.desc", "limit": "1"})
        if runs:
            r0 = runs[0]
            meta_sync = {"at": r0.get("started_at"), "status": r0.get("status"),
                         "unmapped": r0.get("unmapped_campaigns"),
                         "rows": r0.get("rows_upserted"),
                         "since": r0.get("since"), "until": r0.get("until")}
        if meta_rows:
            nomap = sum(1 for r in meta_rows if not r["st"])
            print(f"\n  Meta広告: {len(meta_rows)}行"
                  f"（未紐付け {nomap}行 / 消化額 {sum(r['sp'] for r in meta_rows):,}円）")
    except Exception as _e:
        print(f"  （Meta広告の取得に失敗しました。広告タブは空になります: {_e}）")

    # --- Meta広告（日 × 広告＝クリエイティブ）-------------------------------
    #   どのクリエイティブが効いているかを見るための一覧。日次のまま全期間を載せると
    #   data.json が太るので、直近 META_AD_DAYS 日ぶんだけにする（クリエイティブは
    #   入れ替わりが早く、古いものを比べても打ち手にならないため）。
    #   ここも実額だけを持つ。CTR などの比率は画面側で 実額÷実額 で出す。
    meta_ads: list[dict] = []
    try:
        since = (datetime.now(JST).date() - timedelta(days=META_AD_DAYS)).isoformat()
        # ad_id / account_id は広告マネージャへのリンクを作るのに要る
        #（数字を見てそのまま直しに行けるように、クリエイティブ名からリンクする）。
        ADBASE = ("date,ad_id,account_id,ad_name,campaign_name,destination_id,"
                  "spend,impressions,reach,clicks")
        # profile_visits はクリエイティブ別のCPC（＝広告費÷プロフアクセス数）に要る。
        # 実在する列だけを選ぶ（列が増えたら自動で拾う。無くても落ちない）。
        ADOPTIONAL = ["video_avg_time_watched", "video_plays", "profile_visits",
                      "follows", "likes", "landing_page_views"]
        src = _select_all(sb, "meta_insights_daily_ad",
                          _select_cols(sb, "meta_insights_daily_ad", ADBASE, ADOPTIONAL),
                          order="date", extra={"date": f"gte.{since}"})
        for r in src:
            did = r.get("destination_id")
            meta_ads.append({
                "d": str(r["date"]),
                "an": r.get("ad_name") or "(名称なし)",
                # ai=広告ID / ac=広告アカウントID。広告マネージャのURLに使う
                "ai": str(r["ad_id"]) if r.get("ad_id") is not None else None,
                "ac": str(r["account_id"]) if r.get("account_id") is not None else None,
                "c": r.get("campaign_name") or "(名称なし)",
                "st": dest_name.get(str(did)) if did else None,
                "si": dest_sid.get(str(did)) if did else None,
                "sp": round(_num(r.get("spend"))),
                "im": int(r.get("impressions") or 0),
                "rc": int(r.get("reach") or 0),
                "ck": int(r.get("clicks") or 0),
            })
            if r.get("video_avg_time_watched") is not None:
                meta_ads[-1]["vt"] = float(r["video_avg_time_watched"] or 0)
            if r.get("video_plays") is not None:
                meta_ads[-1]["vp"] = int(r["video_plays"] or 0)
            if r.get("landing_page_views") is not None:
                meta_ads[-1]["lp"] = int(r["landing_page_views"] or 0)
            # 未取得の日は付けない（付けないと画面で「—」になる。0にはしない）
            if r.get("profile_visits") is not None:
                meta_ads[-1]["pv"] = int(r["profile_visits"] or 0)
            if r.get("follows") is not None:
                meta_ads[-1]["fl"] = int(r["follows"] or 0)
            if r.get("likes") is not None:
                meta_ads[-1]["lk"] = int(r["likes"] or 0)
        if meta_ads:
            names = len({r["an"] for r in meta_ads})
            print(f"  Meta広告(クリエイティブ): {len(meta_ads)}行 / {names}種"
                  f"（{since} 以降）")
    except Exception as _e:
        print(f"  （クリエイティブ別は取れませんでした。その欄は空になります: {_e}）")

    # --- 割り当て先に選べる店舗（納品先）------------------------------------
    #   未紐付けキャンペーンを画面から割り当てるときのプルダウン用。
    #   POS店舗と結び付いている納品先だけを出す（結び付いていない先を選んでも
    #   売上と突き合わせられず、選べてしまうと事故のもとになるため）。
    dests: list[dict] = []
    try:
        for d in _select_all(sb, "destinations", "id,name", order="name"):
            did = str(d["id"])
            if did in dest_sid:
                dests.append({"id": did, "name": d.get("name") or did,
                              "s": dest_sid[did], "ow": dest_own.get(did)})
    except Exception:
        pass

    return {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "meta": meta_rows,
        "metaAds": meta_ads,
        "metaDests": dests,
        "metaSync": meta_sync,
        "stores": [{"id": s["id"], "name": name_by_id.get(s["id"], str(s["id"])),
                    "own": own_by_id.get(s["id"]),      # null＝区分が未設定
                    "kv": kv_by_id.get(s["id"], True),
                    "visible": vis_by_id.get(s["id"], True)}
                   for s in stores],
        "daily": daily_rows,
        "cat": cat_rows,
        "catp": catprice_rows,
        "bundles": bundle_rows,
        "bundleNames": bundle_names,
        "catSeason": cat_season,
        "gradeBands": grade_bands,
    }


# ------------------------------------------------------------
# Supabase Storage への配信
# ------------------------------------------------------------
def _ensure_bucket(sb: Supabase, bucket: str, public: bool) -> None:
    url = f"{sb.url}/storage/v1/bucket"
    resp = sb.session.post(url, data=json.dumps(
        {"id": bucket, "name": bucket, "public": public}), timeout=60)
    kind = "公開" if public else "非公開"
    if resp.status_code in (200, 201):
        print(f"  {kind}バケット '{bucket}' を作成しました")
    elif resp.status_code in (400, 409):
        print(f"  {kind}バケット '{bucket}' は既にあります")
    else:
        raise EtlError(f"バケット作成に失敗（HTTP {resp.status_code}）: {resp.text[:300]}")


def _upload(sb: Supabase, bucket: str, path: str, body: bytes,
            content_type: str, public: bool) -> str:
    import requests
    url = f"{sb.url}/storage/v1/object/{bucket}/{path}"
    # ⚠️ 生バイト(raw body)で送ると、こちらが Content-Type: text/html を指定しても
    #    Supabase 側が text/plain のまま保存してしまい、ブラウザが「ページ」ではなく
    #    「ソース文字列」を表示する不具合になる（実測で確認）。
    #    公式クライアント(storage-js)と同じ multipart/form-data で「ファイルの種類」を
    #    明示して送ると正しく text/html で保存・配信される。
    #    念のため一度消してから新規作成し、古い種類情報が残らないようにする。
    dresp = requests.delete(url, headers={"apikey": sb.key,
                            "Authorization": f"Bearer {sb.key}"}, timeout=60)
    print(f"  （削除: HTTP {dresp.status_code} {dresp.text[:80]}）")
    filename = path.split("/")[-1]
    # 公式クライアント(storage-js/storage3)と同じ形にする：
    #   ・cache-control は「ヘッダ」で渡す（フォーム項目にしない）
    #   ・本文はファイル1つだけの multipart（種類を明示）
    # こうしないと保存メタデータの mimetype が空になり、配信が text/plain になる。
    resp = requests.post(
        url,
        headers={"apikey": sb.key, "Authorization": f"Bearer {sb.key}",
                 "x-upsert": "true", "cache-control": "max-age=0"},
        files={"file": (filename, body, content_type)},    # ← ファイルの種類を明示
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise EtlError(f"{path} のアップロードに失敗（HTTP {resp.status_code}）: {resp.text[:300]}")
    if public:
        loc = f"{sb.url}/storage/v1/object/public/{bucket}/{path}"
    else:
        loc = f"{bucket}/{path}（非公開・ログインした社内メンバーのみ）"
    print(f"  配信: {loc}")
    return loc


def _verify_served(sb: Supabase, bucket: str, path: str,
                   public_url: str, cache_bust: str = "") -> None:
    """配信後、実際に配信されるContent-Typeと、保存されている本当の種類を確認する。"""
    import requests
    # ① ブラウザが受け取る種類（公開URL＝CDN経由）
    for label, url in [("通常", public_url),
                       ("cache回避", f"{public_url}?cb={cache_bust}")]:
        try:
            r = requests.get(url, timeout=60)
            ct = r.headers.get("Content-Type")
            age = r.headers.get("age") or "-"
            cc = r.headers.get("cache-control") or "-"
            ok = "✅html" if (ct and "text/html" in ct) else "⚠️not-html"
            print(f"  検証[{label}]: HTTP {r.status_code} / CT={ct} / age={age} / cc={cc} / {ok}")
        except Exception as e:
            print(f"  検証[{label}]をスキップ: {e}")
    # ② 保存されている本当の種類（infoエンドポイント＝CDNを通さない原本）
    try:
        info_url = f"{sb.url}/storage/v1/object/info/public/{bucket}/{path}"
        r = requests.get(info_url, headers={"apikey": sb.key,
                         "Authorization": f"Bearer {sb.key}"}, timeout=60)
        print(f"  保存種類[info]: HTTP {r.status_code} / {r.text[:500]}")
    except Exception as e:
        print(f"  保存種類[info]をスキップ: {e}")
    # ③ 原本の配信種類（認証ダウンロード＝公開CDNを通さない）
    try:
        au = f"{sb.url}/storage/v1/object/authenticated/{bucket}/{path}"
        r = requests.get(au, headers={"apikey": sb.key,
                         "Authorization": f"Bearer {sb.key}"}, timeout=60)
        print(f"  原本配信[auth]: HTTP {r.status_code} / CT={r.headers.get('Content-Type')}")
    except Exception as e:
        print(f"  原本配信[auth]をスキップ: {e}")


def _delete(sb: Supabase, bucket: str, path: str) -> None:
    """公開バケットに残った古いファイルを確実に消す（認証モードで漏れを防ぐ）。
    単体DELETEと一括removeの両方を叩き、実ステータスをログに出す（空振り検知のため）。"""
    # ① 単体 DELETE
    url = f"{sb.url}/storage/v1/object/{bucket}/{path}"
    try:
        resp = sb.session.delete(url, timeout=60)
        print(f"  公開側 {bucket}/{path} 削除(単体): HTTP {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        print(f"  公開側 単体削除スキップ: {e}")
    # ② 一括 remove（storage3 の remove 相当。prefixes 指定・保険）
    try:
        r2 = sb.session.request(
            "DELETE", f"{sb.url}/storage/v1/object/{bucket}",
            data=json.dumps({"prefixes": [path]}), timeout=60)
        print(f"  公開側 {bucket}/{path} 削除(一括): HTTP {r2.status_code} {r2.text[:120]}")
    except Exception as e:
        print(f"  公開側 一括削除スキップ: {e}")


def _inject_config(html: str, supa_url: str, anon: str, public_data_url: str) -> bytes:
    """HTMLのプレースホルダを実値に置き換える。"""
    return (html
            .replace("__SUPABASE_URL__", supa_url)
            .replace("__SUPABASE_ANON_KEY__", anon)
            .replace("__PUBLIC_DATA_URL__", public_data_url)
            ).encode("utf-8")


def write_dash_tables(sb: Supabase, data: dict) -> None:
    """集計結果を dash_* テーブルにも書く（sql/027）。

    【なぜJSONと二重に持つのか】
      いまは data.json（10.5MB）を丸ごと配って、画面側で絞り込んでいる。
      テーブルなら RLS が効くので「加盟店には自店の行しか返さない」ことを
      Postgres 側で担保できる。アプリの作り方に関係なく守られる。

      この段階では両方に書く。数字が一致することを確かめてから、
      画面をテーブル読みに切り替える。先に片方を止めると戻せない。

    【集計はやり直さない】
      data.json に入れるのと同じ行を、名前だけ付け替えて書く。
      別々に集計すると、必ずどこかで数字がずれる。
    """
    print("\n  集計結果をテーブルにも書きます（dash_*）...")

    daily = [{
        "date": r["d"], "store_id": r["s"],
        "sales_in": r.get("in"), "sales_ex": r.get("ex"),
        "tx": r.get("tx"), "items": r.get("it"), "visitors": r.get("v"),
        "bag_in": r.get("bag"), "bag_ex": r.get("bagEx"), "bag_qty": r.get("bagq"),
        # komono_ex=小物の税抜売上（商品単価の分子から差し引く用）。
        "komono_ex": r.get("komEx"),
        "coupon_qty": r.get("coup"), "coupon_amount": r.get("coupAmt"),
        # exU/exM/vm は該当する店・日にしか付かない。無ければ null のまま。
        "sales_ex_unmanned": r.get("exU"), "sales_ex_manned": r.get("exM"),
        "visitors_staffed": r.get("vm"),
    } for r in data.get("daily", [])]

    cat = [{"date": r["d"], "store_id": r["s"], "category": r["c"],
            "amount": r.get("a"), "qty": r.get("q")} for r in data.get("cat", [])]

    catp = [{"date": r["d"], "store_id": r["s"], "category": r["c"], "price": r["p"],
             "amount": r.get("a"), "qty": r.get("q")} for r in data.get("catp", [])]

    bundle = [{"date": r["d"], "store_id": r["s"], "code": r["code"],
               "n": r.get("n"), "amount": r.get("a")} for r in data.get("bundles", [])]

    # 加盟店向け：画面が要る店舗情報と、事前計算した比較値（sql/028）。
    # store-<id>.json に焼き込んでいるのと同じ中身を、RLSで守れる場所に置く。
    dstore, dbench = [], []
    try:
        from tools.store_bundles import build_store_bundles, KCOLS
        _b = build_store_bundles(data, datetime.now(JST).date().isoformat())
        for sid, b in _b.items():
            m = (b.get("store") or {})
            dstore.append({"store_id": sid, "name": m.get("name") or str(sid),
                           "ownership": m.get("own"), "brand_label": b.get("brandLabel"),
                           "kpi_visitors": m.get("kv", True) is not False,
                           "visible": m.get("visible", True) is not False})
            for preset, v in (b.get("bench") or {}).items():
                for k in KCOLS:
                    dbench.append({"store_id": sid, "preset": preset, "metric": k,
                                   "value": (v.get("top3") or {}).get(k),
                                   "date_from": v.get("from"), "date_to": v.get("to")})
    except Exception as e:                       # noqa: BLE001
        print(f"    ⚠️ 店舗情報・比較値を作れませんでした: {str(e)[:200]}")

    jobs = [
        ("dash_store", dstore, "store_id"),
        ("dash_benchmark", dbench, "store_id,preset,metric"),
        ("dash_daily", daily, "date,store_id"),
        ("dash_category", cat, "date,store_id,category"),
        ("dash_category_price", catp, "date,store_id,category,price"),
        ("dash_bundle", bundle, "date,store_id,code"),
    ]
    for table, rows, key in jobs:
        if not rows:
            print(f"    {table}: 0件（書くものがありません）")
            continue
        try:
            n = sb.upsert(table, rows, on_conflict=key)
            # 送った件数と反映された行数がずれたら、取りこぼしている。
            # data.json と数字が合わなくなるので、その場で気づけるようにする。
            mark = "" if n == len(rows) else f"  ⚠️ {len(rows)-n:,}件が反映されていません"
            print(f"    {table}: {len(rows):,}件を書きました（{n:,}行）{mark}")
        except Exception as e:                   # noqa: BLE001
            # ここで落としても data.json は既にできている。配信は続ける。
            print(f"    ⚠️ {table} に書けませんでした: {str(e)[:200]}")
            print(f"       （sql/027_dashboard_tables.sql を実行済みか確認してください）")


def main() -> int:
    parser = argparse.ArgumentParser(description="ダッシュボードデータの集計と配信")
    parser.add_argument("--print-json", action="store_true",
                        help="data.json の中身を標準出力にも出す")
    parser.add_argument("--no-deploy", action="store_true",
                        help="Storageへの配信をせず集計だけ行う")
    parser.add_argument("--no-tables", action="store_true",
                        help="集計結果のテーブル(dash_*)への書き込みをしない")
    args = parser.parse_args()

    load_dotenv()
    try:
        sb = Supabase()
    except EtlError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    anon = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    auth_mode = bool(anon)
    print(f"配信モード: {'認証（Googleログイン制・社内のみ）' if auth_mode else '公開（URLを知っていれば閲覧可）'}")

    print("ダッシュボード用データを集計しています...")
    data = build_data(sb)
    print(f"  日別×店舗: {len(data['daily'])}件 / カテゴリ日別: {len(data['cat'])}件 / 価格帯別: {len(data.get('catp',[]))}件")
    # カテゴリ別・価格帯別・日別×店舗は data.json から外す（テーブルから読む）。
    # この3つで13万行あり、data.json のほとんどを占めていた。
    # 日別は期間で切らずに全期間を読む：店舗一覧・日付の範囲・推移・着地見込みなど、
    # 画面ごとに要る期間がばらばらで、切ると必ずどこかが足りなくなるため。
    # ※ data 自体からは消さない。加盟店バンドル(store_bundles)がこれを使って
    #   自店ぶんを切り出しているため。ここでは「配る中身」からだけ抜く。
    payload = {k: v for k, v in data.items() if k not in ("cat", "catp", "daily")}
    data_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _full = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    print(f"  data.json サイズ: {len(data_bytes)//1024} KB"
          f"（カテゴリ別・価格帯別を外す前: {_full//1024} KB）")

    if not args.no_tables:
        write_dash_tables(sb, data)

    if args.print_json:
        print("----DATA_JSON_BEGIN----")
        print(data_bytes.decode("utf-8"))
        print("----DATA_JSON_END----")

    if not args.no_deploy:
        html_text = (ROOT / "web" / "dashboard.html").read_text(encoding="utf-8")

        # index.html は常に公開バケット（URLを変えないため）
        _ensure_bucket(sb, BUCKET_PUBLIC, public=True)

        # 静的サブページ（推移・予実・販促・棚卸・分析AI・経営・管理）も毎回配信する。
        # これらは実行時に config.json を自分で読むため設定の埋め込みは不要（そのまま配信）。
        # → dashboard.html だけでなく trends.html 等の更新も、この集計・配信で反映される。
        for _pg in ("trends.html", "pl.html", "promo.html", "stock.html",
                    "ask.html", "keiei.html", "admin.html", "forecast.html"):
            _fp = ROOT / "web" / _pg
            if _fp.exists():
                _upload(sb, BUCKET_PUBLIC, _pg,
                        _fp.read_text(encoding="utf-8").encode("utf-8"),
                        "text/html; charset=utf-8", public=True)

        if auth_mode:
            # data.json は非公開バケットへ。HTMLに公開情報を埋め込む。
            _ensure_bucket(sb, BUCKET_PRIVATE, public=False)
            _upload(sb, BUCKET_PRIVATE, "data.json", data_bytes,
                    "application/json; charset=utf-8", public=False)
            # 加盟店(FC)向け：店舗ごとのバンドル store-<id>.json を書き出す。
            #   中身＝自店の明細＋事前計算した比較集計（他店の生データは含めない）。
            #   RLS（sql/019）で「その店の割当者＋本部」だけが読める。
            try:
                from tools.store_bundles import build_store_bundles
                from datetime import datetime as _dt
                bundles = build_store_bundles(data, _dt.now(JST).date().isoformat())
                for _sid, _b in bundles.items():
                    _body = json.dumps(_b, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    _upload(sb, BUCKET_PRIVATE, f"store-{_sid}.json", _body,
                            "application/json; charset=utf-8", public=False)
                print(f"  店舗バンドル: {len(bundles)}件（store-<id>.json）")
            except Exception as _e:
                print(f"  ⚠️ 店舗バンドル生成をスキップ: {_e}")
            html = _inject_config(html_text, sb.url, anon, "")
            _upload(sb, BUCKET_PUBLIC, "index.html", html,
                    "text/html; charset=utf-8", public=True)
            _delete(sb, BUCKET_PUBLIC, "data.json")  # 公開側の実データを消す
            # 画面が読む公開設定（anon は公開してよい鍵。画面はこれを見て認証モードで動く）
            cfg = json.dumps({"mode": "auth", "url": sb.url, "anon": anon},
                             ensure_ascii=False).encode("utf-8")
            _upload(sb, BUCKET_PUBLIC, "config.json", cfg,
                    "application/json; charset=utf-8", public=True)
            page = f"{sb.url}/storage/v1/object/public/{BUCKET_PUBLIC}/index.html"
            _verify_served(sb, BUCKET_PUBLIC, "index.html", page, cache_bust=data["generated_at"].replace(":",""))
            print(f"\n✅ 配信しました（認証モード）。社内の方は下のURLを開き、"
                  f"@avend.co.jp の Google でログインしてください。\n  {page}")
        else:
            # 従来どおり：公開バケットに両方置く
            html = _inject_config(html_text, "", "", "")
            _upload(sb, BUCKET_PUBLIC, "data.json", data_bytes,
                    "application/json; charset=utf-8", public=True)
            _upload(sb, BUCKET_PUBLIC, "index.html", html,
                    "text/html; charset=utf-8", public=True)
            # 画面が読む公開設定（公開モード：data.json の公開URLを指す）
            cfg = json.dumps({"mode": "public",
                              "dataUrl": f"{sb.url}/storage/v1/object/public/{BUCKET_PUBLIC}/data.json"},
                             ensure_ascii=False).encode("utf-8")
            _upload(sb, BUCKET_PUBLIC, "config.json", cfg,
                    "application/json; charset=utf-8", public=True)
            page = f"{sb.url}/storage/v1/object/public/{BUCKET_PUBLIC}/index.html"
            _verify_served(sb, BUCKET_PUBLIC, "index.html", page, cache_bust=data["generated_at"].replace(":",""))
            print(f"\n✅ 配信しました（公開モード）。ブラウザで下のURLを開いてください。\n  {page}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
