/* ============================================================
 *  landing.js — 着地見込みの「唯一の実装」（全ページ共有）
 *
 *  アプリ内のどのページ（ダッシュボード・予実・経営レポート・推移）でも、
 *  着地見込みの数字は必ずここだけで計算する。ページごとに独自ロジックを持たない。
 *
 *  定義（アプリ共通）:
 *    着地見込み(税抜売上) ＝ ① 今月の経過実績（税抜）
 *                        ＋ ② 平日の直近10日平均売上 × 残りの平日数
 *                        ＋ ③ 土日祝の直近6日平均売上 × 残りの土日祝数
 *    ・平日/土日祝＝土日＋日本の祝日。
 *    ・「直近N日」＝ 締日（取込済みの最新日）以前で、その区分の売上がある日を新しい順に最大N日。
 *    ・平日/土日祝の平均売上（税抜/日）は手入力で上書き可（plan_data page="fcavg"）。
 *      保存された店・月は自動値より手入力値を優先する（同じDBを全ページが読む）。
 *    ・税込売上の着地は「税抜着地 ÷ 実績税率」。集計は各店の着地を合算。
 *
 *  データ源（＝どのページも同じ）:
 *    ・日次実績: DATA.daily（dash_daily 由来。行 {s:店id, d:日付, in:税込, ex:税抜, tx, it, v}）
 *    ・手入力の上書き: plan_data page="fcavg"（scope=店id, ym）data={wkEx, weEx}
 * ============================================================ */
(function (global) {
  "use strict";

  var WK_DAYS = 10, WE_DAYS = 6;   // 自動見込みの平均に使う直近日数（平日10日 / 土日祝6日）

  // 日本の祝日（土日祝の判定用）。年度が進んだら追記。全ページ共通で必ずここだけを使う。
  var JP_HOLIDAYS = new Set([
    "2025-08-11","2025-09-15","2025-09-23","2025-10-13","2025-11-03","2025-11-23","2025-11-24",
    "2026-01-01","2026-01-12","2026-02-11","2026-02-23","2026-03-20","2026-04-29",
    "2026-05-03","2026-05-04","2026-05-05","2026-05-06","2026-07-20","2026-08-11",
    "2026-09-21","2026-09-22","2026-09-23","2026-10-12","2026-11-03","2026-11-23",
    "2027-01-01","2027-01-11","2027-02-11","2027-02-23","2027-03-21","2027-03-22"
  ]);

  function isWkHol(d) { var g = new Date(d + "T00:00:00Z").getUTCDay(); return g === 0 || g === 6 || JP_HOLIDAYS.has(d); }
  function dayClass(d) { return isWkHol(d) ? "we" : "wd"; }
  function addDays(d, n) { var t = new Date(d + "T00:00:00Z"); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); }
  function monthEnd(ym) { var p = ym.split("-").map(Number); var last = new Date(Date.UTC(p[0], p[1], 0)).getUTCDate(); return ym + "-" + String(last).padStart(2, "0"); }
  function today() { var d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function exOf(r) { return (r.ex != null ? r.ex : (r.in || 0) / 1.1); }   // 税抜（無い店は税込÷1.1で近似）

  var METS = ["in", "ex", "tx", "it", "v", "txv"];

  // ある店集合(ids)の当月コンポーネント（締日までの実績・平日/土日祝の1日平均・残日数・実績税率）。
  // pooled（合算）で返す。KPI別（in/ex/tx/it/v/txv）に平均を持つ。kvOf(店id)=来店計測店か（v/txvの加算判定）。
  function components(daily, ids, ym, kvOf) {
    var mS = ym + "-01", mE = monthEnd(ym), td0 = today(), winStart = addDays(mS, -90);
    var byDate = {};
    for (var i = 0; i < daily.length; i++) {
      var r = daily[i]; if (!ids.has(r.s)) continue; if (r.d < winStart || r.d > mE) continue;
      var a = byDate[r.d] || (byDate[r.d] = { in: 0, ex: 0, tx: 0, it: 0, v: 0, txv: 0 });
      a.in += r.in || 0; a.ex += exOf(r); a.tx += r.tx || 0; a.it += r.it || 0;
      var kv = kvOf ? kvOf(r.s) : true;
      if (kv) { a.txv += r.tx || 0; if (r.v != null) a.v += r.v; }
    }
    var dates = Object.keys(byDate).sort();
    var monthDates = dates.filter(function (d) { return d >= mS && d <= mE; });
    var cutoff = td0 < mE ? td0 : mE;
    var closing = monthDates.filter(function (d) { return d <= cutoff; }).slice(-1)[0] || null;
    var actual = { in: 0, ex: 0, tx: 0, it: 0, v: 0, txv: 0 }; var wkN = 0, weN = 0;
    if (closing) for (var j = 0; j < monthDates.length; j++) {
      var d0 = monthDates[j]; if (d0 > closing) continue; var b = byDate[d0];
      for (var m = 0; m < METS.length; m++) actual[METS[m]] += b[METS[m]];
      if (dayClass(d0) === "we") weN++; else wkN++;
    }
    var ref = closing || cutoff, hist = dates.filter(function (d) { return d <= ref; });
    function avg(cls, cap, k) { var s = 0, n = 0; for (var x = hist.length - 1; x >= 0 && n < cap; x--) { var d = hist[x]; if (dayClass(d) === cls) { s += byDate[d][k]; n++; } } return n ? s / n : 0; }
    var A = {}; for (var mm = 0; mm < METS.length; mm++) { var k = METS[mm]; A[k] = { wk: avg("wd", WK_DAYS, k), we: avg("we", WE_DAYS, k) }; }
    var remWk = 0, remWe = 0, start = closing ? addDays(closing, 1) : mS;
    for (var d1 = start; d1 <= mE; d1 = addDays(d1, 1)) { if (dayClass(d1) === "we") remWe++; else remWk++; }
    var exRatio = actual.in ? actual.ex / actual.in : 0.91;
    return { actual: actual, avg: A, remWk: remWk, remWe: remWe, exRatio: exRatio, closing: closing, wkN: wkN, weN: weN, hasData: dates.length > 0 };
  }

  // 単店の税抜着地の内訳（手入力ovで平均を上書き）。ov={wkEx,weEx} or null。
  function storeParts(daily, id, ym, ov) {
    var mS = ym + "-01", mE = monthEnd(ym), td0 = today(), winStart = addDays(mS, -90);
    var bd = {};
    for (var i = 0; i < daily.length; i++) { var r = daily[i]; if (r.s !== id) continue; if (r.d < winStart || r.d > mE) continue; bd[r.d] = (bd[r.d] || 0) + exOf(r); }
    var dates = Object.keys(bd).sort();
    if (!dates.length) return null;
    var monthDates = dates.filter(function (d) { return d >= mS && d <= mE; });
    var cutoff = td0 < mE ? td0 : mE;
    var closing = monthDates.filter(function (d) { return d <= cutoff; }).slice(-1)[0] || null;
    var actual = 0; if (closing) for (var j = 0; j < monthDates.length; j++) { var d0 = monthDates[j]; if (d0 <= closing) actual += bd[d0]; }
    var ref = closing || cutoff, hist = dates.filter(function (d) { return d <= ref; });
    function avg(cls, cap) { var s = 0, n = 0; for (var x = hist.length - 1; x >= 0 && n < cap; x--) { var d = hist[x]; if (dayClass(d) === cls) { s += bd[d]; n++; } } return n ? s / n : 0; }
    var wkAuto = avg("wd", WK_DAYS), weAuto = avg("we", WE_DAYS);
    var wk = (ov && Number.isFinite(ov.wkEx)) ? ov.wkEx : wkAuto;
    var we = (ov && Number.isFinite(ov.weEx)) ? ov.weEx : weAuto;
    var remWk = 0, remWe = 0, start = closing ? addDays(closing, 1) : mS;
    for (var d1 = start; d1 <= mE; d1 = addDays(d1, 1)) { if (dayClass(d1) === "we") remWe++; else remWk++; }
    return { actual: actual, remWk: remWk, remWe: remWe, wkAuto: wkAuto, weAuto: weAuto, wkUsed: wk, weUsed: we, closing: closing, value: actual + remWk * wk + remWe * we };
  }

  // 税抜着地（複数店＝各店の着地を合算。手入力を店ごとに反映）。ovGet(店id)->{wkEx,weEx}|null。
  function salesEx(daily, ids, ym, ovGet) {
    if (!ids || !ids.size) return null;
    var total = 0, any = false;
    ids.forEach(function (id) {
      var p = storeParts(daily, id, ym, ovGet ? ovGet(id) : null);
      if (p) { total += p.value; any = true; }
    });
    return any ? total : null;
  }

  // 単店の自動（手入力なし）全指標着地。① 経過実績 ＋ 各区分の1日平均 × 残日数。
  function storeMetricsAuto(daily, id, ym, kvOf) {
    var C = components(daily, new Set([id]), ym, kvOf);
    if (!C.hasData) return null;
    var L = {};
    for (var i = 0; i < METS.length; i++) { var k = METS[i]; L[k] = C.actual[k] + C.remWk * C.avg[k].wk + C.remWe * C.avg[k].we; }
    L.exRatio = C.exRatio;
    return L;
  }
  // 単店の全指標の着地。手入力(ov)で税抜売上が変わると、数量系(税込in・点数it・取引tx・来店v・txv)は
  // 「同じ倍率 k = 上書き税抜売上 ÷ 自動税抜売上」でスケールする。
  //   ⇒ 客単価(in/tx)・商品単価(ex/it)・平均購入数(it/tx)・購入率(txv/v) は k で不変＝現状維持。
  function landingFull(daily, id, ym, ov, kvOf) {
    var auto = storeMetricsAuto(daily, id, ym, kvOf);
    if (!auto) return null;
    var ex = auto.ex, k = 1;
    if (ov && Number.isFinite(ov.wkEx) && Number.isFinite(ov.weEx)) {
      var p = storeParts(daily, id, ym, ov);
      if (p) { ex = p.value; k = auto.ex > 0 ? ex / auto.ex : 1; }
    }
    return { ex: ex, in: auto.in * k, it: auto.it * k, tx: auto.tx * k, txv: auto.txv * k, v: auto.v * k, k: k, exRatio: auto.exRatio };
  }
  // 複数店の全指標着地＝各店を（各店の倍率で）合算。ovGet(店id)->{wkEx,weEx}|null。
  function aggFull(daily, ids, ym, ovGet, kvOf) {
    if (!ids || !ids.size) return null;
    var sum = { ex: 0, in: 0, it: 0, tx: 0, txv: 0, v: 0 }, any = false;
    ids.forEach(function (id) {
      var L = landingFull(daily, id, ym, ovGet ? ovGet(id) : null, kvOf);
      if (L) { any = true; sum.ex += L.ex; sum.in += L.in; sum.it += L.it; sum.tx += L.tx; sum.txv += L.txv; sum.v += L.v; }
    });
    return any ? sum : null;
  }

  // plan_data page="fcavg"（手入力の平日/土日祝 平均売上・税抜）を全店/全月まとめて読む。
  // 戻り: { "店id|YYYY-MM": {wkEx, weEx} }
  function loadOverrides(SB) {
    if (!SB) return Promise.resolve({});
    return SB.from("plan_data").select("scope,ym,data").eq("page", "fcavg").then(function (res) {
      var m = {}; (res.data || []).forEach(function (r) { var d = r.data || {}; if (Number.isFinite(d.wkEx) && Number.isFinite(d.weEx)) m[String(r.scope) + "|" + r.ym] = d; }); return m;
    }, function () { return {}; });
  }

  global.NL = {
    WK_DAYS: WK_DAYS, WE_DAYS: WE_DAYS, JP_HOLIDAYS: JP_HOLIDAYS,
    isWkHol: isWkHol, dayClass: dayClass, addDays: addDays, monthEnd: monthEnd, today: today,
    components: components, storeParts: storeParts, salesEx: salesEx, loadOverrides: loadOverrides,
    storeMetricsAuto: storeMetricsAuto, landingFull: landingFull, aggFull: aggFull
  };
})(typeof window !== "undefined" ? window : this);
