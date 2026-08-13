"""店舗別データバンドル（store-<id>.json）を作る。純粋関数（Supabase非依存＝単体テスト可）。

加盟店（FC）には「自店の明細 ＋ 匿名化した比較集計（数字のみ）」だけを配る。
他店の日別・店名などの生データは一切含めない（＝通信を覗いても競合の生データは見えない）。

比較値（同ブランド上位5平均／全店平均／FC上位3平均）は、画面(dashboard.html)の
peerAvg と同じロジックを Python に移植して事前計算する。よって本部ページと数字が一致する。
比較はプリセット期間（前日/今週/今月/前週/前月）ぶんを焼き込む（加盟店の画面はこの5期間）。
"""
from __future__ import annotations
from datetime import date, timedelta

# 画面の KPI2_COLS と同じ並び・同じ定義
KCOLS = ["ex", "v", "pr", "aov", "qty", "upt", "aup"]


def _sum_rows(rows, kv_of, visit_stores):
    """dashboard.html の sumRows を移植（KPI2_COLS に必要な項目のみ）。"""
    p = {"in": 0.0, "ex": 0.0, "tx": 0, "txv": 0, "it": 0.0, "txIt": 0,
         "v": 0, "bagEx": 0.0, "komEx": 0.0, "nocatEx": 0.0,
         "hasV": False, "hasEx": False, "hasIt": False}
    for r in rows:
        p["in"] += r.get("in") or 0
        p["tx"] += r.get("tx") or 0
        if r.get("ex") is not None:
            p["ex"] += r["ex"]; p["hasEx"] = True
        if r.get("it") is not None:
            p["it"] += r["it"]; p["hasIt"] = True; p["txIt"] += r.get("tx") or 0
        # レジ袋・小物・明細なし の税抜（商品単価の分子から差し引く用。dashboard.html と同じ定義）
        p["bagEx"] += r.get("bagEx") or 0
        p["komEx"] += r.get("komEx") or 0
        p["nocatEx"] += r.get("nocatEx") or 0
        if kv_of(r["s"]) and r["s"] in visit_stores:
            p["txv"] += r.get("tx") or 0
            if r.get("v") is not None:
                p["v"] += r["v"]; p["hasV"] = True
    if not p["hasV"]:
        p["v"] = 0
    return p


def _calc(k, p):
    """dashboard.html の METRICS(M(k).calc) を移植。"""
    if k == "ex":
        return p["ex"] if p["hasEx"] else (p["in"] / 1.1 if p["in"] is not None else None)
    if k == "v":
        return p["v"] if p["hasV"] else None
    if k == "tx":
        return p["tx"]
    if k == "qty":
        return p["it"] if p["hasIt"] else None
    if k == "pr":
        return (p["txv"] / p["v"] * 100) if (p["hasV"] and p["v"] > 0) else None
    if k == "aov":
        return ((p["ex"] if p["hasEx"] else (p["in"] or 0) / 1.1) / p["tx"]) if p["tx"] > 0 else None
    if k == "upt":
        return (p["it"] / p["txIt"]) if (p["hasIt"] and p["txIt"] > 0) else None
    if k == "aup":
        # 商品単価＝(税抜売上−レジ袋−小物−明細なし売上)÷点数。分母(点数)が持たない売上を分子からも除く。
        return ((p["ex"] - p["bagEx"] - p["komEx"] - p["nocatEx"]) / p["it"]) if (p["hasEx"] and p["it"] > 0) else None
    return None


def _presets(today_str, dmin, dmax):
    """dashboard.html の presetRange と同じ期間を [from,to] で返す（データ範囲にクランプ）。"""
    def clamp(dt):
        s = dt.isoformat()
        if s < dmin:
            return dmin
        if s > dmax:
            return dmax
        return s
    today = date.fromisoformat(today_str)
    mon = today - timedelta(days=today.weekday())              # 月曜
    first = today.replace(day=1)
    pm_last = first - timedelta(days=1)                        # 前月末
    pm_first = pm_last.replace(day=1)
    y1 = today - timedelta(days=1)
    lmon = mon - timedelta(days=7)
    out = {
        "prevday":   (clamp(y1), clamp(y1)),
        "thisweek":  (clamp(mon), clamp(today)),
        "thismonth": (clamp(first), clamp(today)),
        "lastweek":  (clamp(lmon), clamp(lmon + timedelta(days=6))),
        "lastmonth": (clamp(pm_first), clamp(pm_last)),
    }
    # from>to（データ範囲外）になった場合は from=to に寄せる
    return {k: (min(f, t), t) for k, (f, t) in out.items()}


def build_store_bundles(data: dict, today_str: str) -> dict:
    """data(dict) から {store_id: bundle_dict} を作る。"""
    stores = data.get("stores", [])
    daily = data.get("daily", [])
    if not daily:
        return {}
    meta = {s["id"]: s for s in stores}
    by_store: dict = {}
    for r in daily:
        by_store.setdefault(r["s"], []).append(r)
    dates = sorted({r["d"] for r in daily})
    dmin, dmax = dates[0], dates[-1]
    visit_stores = {r["s"] for r in daily if r.get("v") is not None}

    def name_of(sid): return (meta.get(sid, {}).get("name") or "")
    def own_of(sid):  return (meta.get(sid, {}).get("own") or "直営")
    def kv_of(sid):   return meta.get(sid, {}).get("kv", True) is not False
    def visible_of(sid): return meta.get(sid, {}).get("visible", True) is not False

    def brand_of(sid):
        n = name_of(sid)
        if n.startswith("SELFURUGI"):
            return "SELFURUGI"
        if n.startswith("NOTIME"):
            return "NOTIME"
        return "その他"

    def rptbrand3(sid):   # 画面の rptBrand3 と一致（GARAGE を分離）
        n = name_of(sid)
        if "GARAGE" in n:
            return "SELFURUGI GARAGE"
        if "SELFURUGI" in n:
            return "SELFURUGI"
        if "NOTIME" in n:
            return "NOTIME"
        return "その他"

    usable = [s["id"] for s in stores if visible_of(s["id"]) and s["id"] in by_store]

    def rows_in(f, t, sid):
        return [r for r in by_store.get(sid, []) if f <= r["d"] <= t]

    def store_kpi(k, f, t, sid):
        return _calc(k, _sum_rows(rows_in(f, t, sid), kv_of, visit_stores))

    def peer_avg(k, f, t, ids):
        vals = [store_kpi(k, f, t, i) for i in ids]
        vals = [v for v in vals if v is not None]
        return (sum(vals) / len(vals)) if vals else None

    def top_by(ids, f, t, n):
        scored = [(i, store_kpi("ex", f, t, i) or 0) for i in ids]
        scored = [x for x in scored if x[1] > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [i for i, _ in scored[:n]]

    presets = _presets(today_str, dmin, dmax)

    def bundle_for(sid):
        own = own_of(sid)
        b3 = rptbrand3(sid)
        not_shimo = lambda i: "下北沢" not in name_of(i)
        # 画面 drawKpi2Detail と同じ比較対象：FC店→同ブランドのFC店のみ／直営店→直営どうし（下北沢除外）
        members = [i for i in usable if not_shimo(i) and
                   ((own_of(i) == "直営") if own == "直営"
                    else (rptbrand3(i) == b3 and own_of(i) != "直営"))]
        bench = {}
        for key, (f, t) in presets.items():
            top3 = top_by(members, f, t, 3)
            bench[key] = {"from": f, "to": t,
                          "top3": {k: peer_avg(k, f, t, top3) for k in KCOLS}}
        return {
            "generated_at": data.get("generated_at"),
            "store": meta.get(sid),
            "stores": [meta.get(sid)],                # usableStores() がこの店だけを返すように
            "brandLabel": ("直営" if own == "直営" else b3),
            "daily": by_store.get(sid, []),
            "cat":  [r for r in data.get("cat", []) if r["s"] == sid],
            "catp": [r for r in data.get("catp", []) if r["s"] == sid],
            "bundles": [r for r in data.get("bundles", []) if r["s"] == sid],
            "bundleNames": data.get("bundleNames", {}),
            "catSeason": data.get("catSeason", {}),
            "gradeBands": data.get("gradeBands", []),
            "bench": bench,
        }

    return {sid: bundle_for(sid) for sid in usable}
