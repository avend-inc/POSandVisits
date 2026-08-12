/*
 * 広告タブの集計（売上比率・来店の突き合わせ）を、実物のコードで確かめる。
 *
 *     node tools/check_ads_math.js
 *
 * 【なぜ要るのか】
 *   広告は「店舗 × 日 × キャンペーン」で複数行になるのに、売上と来店は
 *   「店舗 × 日」に1つしかない。素直に足すとキャンペーン数だけ売上が
 *   膨らみ、売上比率が実際より小さく出る。目で見て気づきにくい壊れ方なので、
 *   dashboard.html の中の関数そのものを取り出して確かめる。
 *
 *   コピーではなく実物を読むので、向こうを直せばこちらも一緒に動く。
 *   関数名や作りを変えて取り出せなくなったときは、その場で止まる。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const HTML = path.join(__dirname, "..", "web", "dashboard.html");
const src = fs.readFileSync(HTML, "utf8");

// dashboard.html から集計まわりだけを切り出す（AD_SALES の宣言〜adVal の手前）
const from = src.indexOf("let AD_BYSTORE=null;");
const to = src.indexOf("function adVal(");
if (from < 0 || to < 0 || to <= from) {
  console.error("❌ dashboard.html から集計部分を取り出せませんでした。");
  console.error("   （adStoreIdx / adZero / adAdd / adSales / adSum の並びが変わった可能性があります）");
  process.exit(1);
}
const code = src.slice(from, to);
for (const name of ["adStoreIdx", "adZero", "adAdd", "adSales", "adSum"]) {
  if (!code.includes("function " + name)) {
    console.error(`❌ ${name} が見つかりません。`);
    process.exit(1);
  }
}

// 売上比率の計算式も実物から取り出す（AD_METRICS の sr 行）
const srLine = src.split("\n").find((l) => l.includes('{k:"sr"'));
if (!srLine) { console.error("❌ 売上比率(sr)の定義が見つかりません。"); process.exit(1); }
const srCalc = /calc:(p=>[^,]+)/.exec(srLine);
if (!srCalc) { console.error("❌ 売上比率の計算式を取り出せません。"); process.exit(1); }

const ctx = { DATA: null, console };
vm.createContext(ctx);
vm.runInContext(code + "\nvar SR=" + srCalc[1] + ";", ctx);
// AD_BYSTORE は let 宣言なので ctx のプロパティにならない。中に入って消す。
const setDaily = (daily) => {
  ctx.DATA = { daily };
  vm.runInContext("AD_BYSTORE=null;", ctx);
};
ctx.state = { from: "2026-08-01", to: "2026-08-31" };

let ng = 0;
const eq = (label, got, want) => {
  const ok = Math.abs((got == null ? NaN : got) - want) < 1e-9 ||
             (got == null && want == null);
  console.log(`  ${ok ? "OK " : "NG "} ${label}: ${got}（期待 ${want}）`);
  if (!ok) ng++;
};

console.log("広告タブの集計チェック");

// 店舗1は 8/8・8/9・8/10 に売上がある。広告を出したのは 8/8 だけ。
setDaily([
  { s: 1, d: "2026-08-08", ex: 100000, v: 50 },
  { s: 1, d: "2026-08-09", ex: 200000, v: 80 },
  { s: 1, d: "2026-08-10", ex: 300000, v: 90 },
  { s: 2, d: "2026-08-08", ex: 50000, v: 20 },
]);

// 同じ店・同じ日に3本のキャンペーンが走っているケース
const rows = [
  { d: "2026-08-08", si: 1, sp: 1000, im: 10, rc: 5, ck: 2 },
  { d: "2026-08-08", si: 1, sp: 2000, im: 10, rc: 5, ck: 2 },
  { d: "2026-08-08", si: 1, sp: 3000, im: 10, rc: 5, ck: 2 },
];
let tot = ctx.adSum(rows, "2026-08-08", "2026-08-10");
eq("キャンペーン3本ぶんの広告費は足す", tot.sp, 6000);
eq("売上はキャンペーン数ぶん膨らませない", tot.ex, 600000);
eq("来店も膨らませない", tot.vis, 220);
eq("売上比率＝6,000÷600,000", ctx.SR(tot), 1);

// 広告を出していない日(8/9・8/10)の売上も分母に入る＝店舗ページとそろう
tot = ctx.adSum(rows, "2026-08-08", "2026-08-08");
eq("期間を8/8だけに絞れば、その日の売上だけ", tot.ex, 100000);
eq("そのときの売上比率＝6,000÷100,000", ctx.SR(tot), 6);

// 店をまたぐと、それぞれの店の売上が足される
tot = ctx.adSum([
  { d: "2026-08-08", si: 1, sp: 1000 },
  { d: "2026-08-08", si: 2, sp: 1000 },
], "2026-08-08", "2026-08-08");
eq("店をまたぐと売上は合算", tot.ex, 150000);
eq("来店も合算", tot.vis, 70);

// 未紐付け（si が無い）行は売上を持ってこられない → 比率は「—」
tot = ctx.adSum([{ d: "2026-08-08", si: null, sp: 5000 }], "2026-08-08", "2026-08-08");
eq("未紐付けの広告費は数える", tot.sp, 5000);
eq("未紐付けは売上が付かない", tot.ex, 0);
eq("売上が無ければ売上比率は出さない", ctx.SR(tot), null);

// 売上が取れない日（デジテール日次のみ＝ex が null）は足さない
setDaily([{ s: 1, d: "2026-08-08", ex: null, v: 30 }]);
tot = ctx.adSum([{ d: "2026-08-08", si: 1, sp: 1000 }], "2026-08-08", "2026-08-08");
eq("税抜売上が無い日は売上に足さない", tot.ex, 0);
eq("来店だけは取れるので数える", tot.vis, 30);

// 同じ集計器を期間を変えて2回使っても、前回ぶんが残らない
setDaily([
  { s: 1, d: "2026-08-08", ex: 100000, v: 50 },
  { s: 1, d: "2026-08-09", ex: 200000, v: 80 },
]);
const acc = ctx.adZero();
ctx.adAdd(acc, { d: "2026-08-08", si: 1, sp: 1000 });
ctx.adSales(acc, "2026-08-08", "2026-08-09");
ctx.adSales(acc, "2026-08-08", "2026-08-08");
eq("集計し直すと前回の売上は消える", acc.ex, 100000);


// --- 段の合計から「売上が無い店」を外しているか -----------------------
//   オープン前の店は広告費だけがあって売上が無い。合計に混ぜると、
//   直営店ぜんたいの売上比率が実態より高く出て、判断を誤らせる。
{
  const secLine = src.split("\n").find((l) => l.includes("const live=part.filter"));
  if (!secLine || !/e\.tot\.ex>0/.test(secLine)) {
    console.log("  NG  段の合計から売上が無い店を外していません");
    ng++;
  } else {
    console.log("  OK  段の合計は売上がある店だけで作っている");
  }
  const exLine = src.split("\n").find((l) => l.includes("const exNote="));
  if (!exLine) { console.log("  NG  外した店を知らせる文がありません"); ng++; }
  else console.log("  OK  外した店は名前と広告費つきで知らせる");
}


// --- クリエイティブ別ランキングの約束ごと ---------------------------
{
  const has = (re, label) => {
    if (re.test(src)) { console.log(`  OK  ${label}`); }
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/const AD_CR_TOP=5;/, "上位5件まで出す");
  has(/const AD_CR_MINSPEND=1000;/, "1日あたり1,000円未満は土俵に乗せない");
  has(/e\.spd<AD_CR_MINSPEND/, "少額の広告をランキングから外している");
  // クリック単価は「安いほうが良い」。降順で並べると最悪が1位になる。
  has(/k:"cpc"[^}]*asc:true/, "クリック単価は安い順に並べる");
  has(/k:"ctr"[^}]*best:"高い"/, "CTRは高い順に並べる");
}

// --- 日別の推移は、期間の全日を並べる -------------------------------
//   データがある日だけだと、期間28日でも軸が数日ぶんしか出ず、
//   期間表示と食い違って日付がおかしく見える。
{
  const line = src.split("\n").find((l) => l.includes("for(let d=state.from; d<=state.to;"));
  if (line) console.log("  OK  日別の推移は期間の全日を並べる");
  else { console.log("  NG  日別の推移が、データのある日だけになっています"); ng++; }
}


// --- カテゴリ別を期間ぶんだけ取る仕組みの約束ごと ---------------------
//   data.json から外したので、取り漏らすと画面が黙って空になる。
//   「足りなければ取りに行って描き直す」経路が残っているかを見る。
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/if\(!catNeed\(hf,ht\)\)return/, "期間が足りなければ空を返して取りに行く（比率表）");
  has(/await ensureCatRange\(state\.from,state\.to\)/, "描く前に期間ぶんを取りにいく");
  has(/function catFromBundle\(\)\{ return !!window\.FRANCHISEE/, "加盟店はバンドルのまま（取り直さない）");
  has(/ensureCatRange\(ymArg\+"-01"/, "レポートは見る月を取りにいく");
}


// --- 「取れない」を 0 で出していないか -------------------------------
//   来店を計測していない店の購入率が 0.0% と並ぶと、売上があるのに
//   買われていないように見える。実績のゼロと区別が付かなくなる。
{
  const bad = src.split("\n").filter((l) => /==null\?0:/.test(l));
  if (bad.length) {
    console.log(`  NG  値が無いところを 0 にして出しています（${bad.length}箇所）`);
    ng++;
  } else console.log("  OK  値が無いところは「—」で出す（0にしない）");
}


// --- 日別×店舗をテーブルから読む ------------------------------------
//   data.json から外したので、読み込みが欠けると画面が丸ごと空になる。
//   全期間を取ること（期間で切ると店舗一覧・日付の範囲が壊れる）を確かめる。
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/DATA\.daily=await loadDaily\(\)/, "日別はテーブルから読み込む");
  has(/async function loadDaily\(\)/, "loadDaily がある");
  // 期間で絞る条件（gte/lte）が入っていたら、全期間でなくなっている
  const fn = src.slice(src.indexOf("async function loadDaily()"),
                       src.indexOf("// 加盟店モード"));
  if (/\.gte\("date"|\.lte\("date"/.test(fn)) {
    console.log("  NG  日別を期間で絞っています（全期間が要ります）"); ng++;
  } else console.log("  OK  日別は全期間を取る");
  // data.json 側の形に戻していること（画面のコードを触らないための約束）
  has(/o=\{d:r\.date, s:r\.store_id/, "data.json と同じ形に戻している");
}


// --- 加盟店をテーブルから読む ----------------------------------------
//   ここが一番効く変更。他店を見せない保証が「ETLの切り出し方」から
//   「PostgresのRLS」に移る。JSONの経路は当面フォールバックとして残す。
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/if\(await franchiseeBootFromTables\(ids\)\)return;/, "加盟店はまずテーブルから読む");
  has(/async function franchiseeBootFromTables/, "テーブル経路がある");
  has(/return false;[\s\S]{0,400}JSONの経路|console\.warn\("加盟店データをテーブルから読めませんでした/,
      "読めないときはJSONに落ちる（黙って空にしない）");
}


// --- 店舗ページの広告（NEWPANEL_STORES 〜 DAY_COLS の実物を動かす）-------
//   広告は「店舗×日×キャンペーン」で複数行あるので、日別の列や
//   平日/土日祝の平均で足し方を間違えると、静かに水増しされる。
{
  const f2 = src.indexOf("const NEWPANEL_STORES=");
  const t2 = src.indexOf("const DAY_COLS=");
  if (f2 < 0 || t2 < 0 || t2 <= f2) {
    console.log("  NG  店舗ページの広告まわりを取り出せませんでした"); ng++;
  } else {
    // 依存している関数は、この検算に必要なぶんだけ用意する（本体は触らない）
    const ctx2 = { console, DATA: null };
    vm.createContext(ctx2);
    vm.runInContext(`
      var DATA=null;
      function storeMeta(id){ return (DATA.stores||[]).find(x=>x.id===id)||null; }
      function rowsIn(from,to,id){ return (DATA.daily||[]).filter(r=>r.s===id&&r.d>=from&&r.d<=to); }
      function dayClass(d){ const w=new Date(d+"T00:00:00Z").getUTCDay(); return (w===0||w===6)?"we":"wd"; }
      function sumRows(rows){ let ex=0,hasEx=false; for(const r of rows){ if(r.ex!=null){ex+=r.ex;hasEx=true;} } return {ex,hasEx}; }
      function M(k){ return {calc:p=>p.hasEx?p.ex:null}; }
    `
      // 実物の adZero / adAdd も入れる（店舗ページの集計はこれを使っている）
      + src.slice(src.indexOf("function adZero()"), src.indexOf("function adSales("))
      + src.slice(f2, t2), ctx2);
    const setMeta = (meta, daily, stores) => {
      ctx2.DATA = { meta, daily, stores: stores || [{ id: 1, name: "いわき" }, { id: 2, name: "山形" }] };
      vm.runInContext("AD_BYSID=null;", ctx2);
    };

    // 同じ日に3本のキャンペーンが走っている。日別の列は合算した1つの値になる。
    setMeta([
      { d: "2026-08-02", si: 1, sp: 1000, im: 100, rc: 50, ck: 5, c: "A" },   // 日曜
      { d: "2026-08-02", si: 1, sp: 2000, im: 100, rc: 50, ck: 5, c: "B" },
      { d: "2026-08-03", si: 1, sp: 3000, im: 200, rc: 80, ck: 4, c: "A" },   // 月曜
      { d: "2026-08-03", si: 2, sp: 9000, im: 999, rc: 999, ck: 9, c: "C" },  // 別の店
    ], [
      { s: 1, d: "2026-08-02", ex: 100000, in: 110000, tx: 10 },
      { s: 1, d: "2026-08-03", ex: 200000, in: 220000, tx: 20 },
      { s: 2, d: "2026-08-03", ex: 500000, in: 550000, tx: 30 },
    ]);

    const day = ctx2.adSpendByDay(1);
    eq("同じ日の複数キャンペーンは1つに合算", day.get("2026-08-02"), 3000);
    eq("他店の広告費は混ざらない", day.get("2026-08-03"), 3000);

    const p = ctx2.adStoreSum(1, "2026-08-02", "2026-08-03");
    eq("期間の広告費", p.sp, 6000);
    eq("表示回数も足す", p.im, 400);
    eq("他店ぶんは入らない", p.ck, 14);
    eq("期間外は入らない", ctx2.adStoreSum(1, "2026-08-03", "2026-08-03").sp, 3000);

    // 売上比率は 実額÷実額。売上が取れない期間は 0 ではなく null
    eq("売上比率＝6,000÷300,000", ctx2.adSrOf(6000, 300000), 2);
    eq("売上が無ければ売上比率は出さない", ctx2.adSrOf(6000, 0), null);
    eq("売上が取れなければ売上比率は出さない", ctx2.adSrOf(6000, null), null);

    // 平日/土日祝の1日あたり。分母は「売上があった日数」＝すぐ上の売上の行と同じ
    const wd = ctx2.adDayAvg(1, "2026-08-02", "2026-08-03", "wd");
    eq("平日は8/3(月)だけ＝3,000円", wd.per, 3000);
    eq("平日の売上比率＝3,000÷200,000", wd.sr, 1.5);
    const we = ctx2.adDayAvg(1, "2026-08-02", "2026-08-03", "we");
    eq("土日祝は8/2(日)だけ＝3,000円", we.per, 3000);
    eq("土日祝の売上比率＝3,000÷100,000", we.sr, 3);

    // 売上が立っていない日は分母に数えない（休業日で薄まらせない）
    setMeta([
      { d: "2026-08-03", si: 1, sp: 1000, c: "A" },
      { d: "2026-08-04", si: 1, sp: 1000, c: "A" },
    ], [
      { s: 1, d: "2026-08-03", ex: 100000, in: 110000, tx: 10 },
      { s: 1, d: "2026-08-04", ex: null, in: 0, tx: 0 },        // 休業
    ]);
    eq("休業日は1日あたりの分母に数えない", ctx2.adDayAvg(1, "2026-08-03", "2026-08-04", "wd").per, 1000);

    // 「いいね率」は 実額÷実額（いいね数 ÷ 表示回数）
    setMeta([
      { d: "2026-08-03", si: 1, sp: 1000, im: 1000, lk: 30, c: "A" },
      { d: "2026-08-04", si: 1, sp: 1000, im: 3000, lk: 10, c: "A" },
    ], [{ s: 1, d: "2026-08-03", ex: 100000, in: 110000, tx: 10 }]);
    {
      const p = ctx2.adStoreSum(1, "2026-08-03", "2026-08-04");
      eq("いいねは足す", p.lk, 40);
      eq("いいね率＝40÷4,000", p.im > 0 ? p.lk / p.im * 100 : null, 1);
    }

    // 平均視聴時間は Meta が返す秒数がそのままDBに入る。こちらで重み付けなどの加工はせず、
    // 値がある行の平均をそのまま出す。
    setMeta([
      { d: "2026-08-03", si: 1, sp: 1000, im: 1000, vt: 10, c: "A" },
      { d: "2026-08-04", si: 1, sp: 1000, im: 9000, vt: 20, c: "A" },
    ], [{ s: 1, d: "2026-08-03", ex: 100000, in: 110000, tx: 10 }]);
    {
      const p = ctx2.adStoreSum(1, "2026-08-03", "2026-08-04");
      eq("平均視聴時間は値がある行の平均", p.vn > 0 ? p.vt / p.vn : null, 15);
    }
    // 視聴時間が無い行（動画でない広告）は数えない。0秒として薄めてしまうため
    setMeta([
      { d: "2026-08-03", si: 1, sp: 1000, im: 1000, vt: 10, c: "A" },
      { d: "2026-08-04", si: 1, sp: 1000, im: 9000, c: "A" },
    ], [{ s: 1, d: "2026-08-03", ex: 100000, in: 110000, tx: 10 }]);
    {
      const p = ctx2.adStoreSum(1, "2026-08-03", "2026-08-04");
      eq("視聴時間が無い行は数えない", p.vn > 0 ? p.vt / p.vn : null, 10);
      eq("　数えた行数", p.vn, 1);
    }

    // 広告を出していない店には列も段も出さない
    setMeta([{ d: "2026-08-03", si: 1, sp: 1000, c: "A" }], []);
    eq("広告のある店", ctx2.adStoreHas(1), true);
    eq("広告の無い店", ctx2.adStoreHas(2), false);
    eq("未紐付け(si=null)は店に付かない", ctx2.adStoreHas(null), false);

    // 段階導入のスイッチ
    eq("いわきには出す", ctx2.newPanelOn(1), true);
    eq("ほかの店にはまだ出さない", ctx2.newPanelOn(2), false);
  }
}

// --- 画面に組み込まれているか（つないだつもりで呼ばれていない事故を防ぐ）---
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/drawDayKpi\(\); drawStoreAds\(\);/, "広告の段を描き直している");
  has(/h\+=kpi2AdRows\(id,/, "KPIまとめに広告の行を足している");
  has(/showAd\?`<th>広告費<\/th>`:""/, "日別推移に広告費の列を足している");
  has(/AD_BYSID=null; AD_BYSTORE=null;/, "割り当てを変えたら索引を作り直す");
  // 加盟店に広告を見せない（データ自体を配っていないが、二重に止める）
  has(/if\(window\.FRANCHISEE\|\|!newPanelOn\(id\)\|\|!adStoreHas\(id\)\)return ""/,
      "加盟店にはKPIまとめの広告行を出さない");
  has(/wxStat=await weatherStats\(id,f,t\)/, "天気をAIのまとめに渡している");
  has(/const auto=autoInsight\(wxStat,weeklyStat\)/, "AIが使えないときも天気・広告・納品を使う");
  has(/sunny\.days<3\|\|rainy\.days<3/, "日数が少ない天気には触れない");
}

// --- 推移グラフの広告費と、広告の段の期間 -----------------------------
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  // 推移グラフ：広告費の系列
  has(/\{key:"ad", label:"広告費"/, "推移グラフに広告費の系列がある");
  has(/data-tmet="ad"/, "推移グラフに広告費のボタンがある");
  has(/if\(k==="ad"\)\{ if\(!adDay\)return null;/, "広告費は日ぶんを合算してから期間で足す");
  // 広告の無い店で「広告費」が選ばれたまま残ると、0円の線が引かれて
  //「使っていない」ではなく「出していない」が実績0に見えてしまう
  has(/state\.trendSel=state\.trendSel\.filter\(x=>x!=="ad"\)/, "広告の無い店では広告費の選択を外す");
  // 「売上推移」は中身が売上だけではないので名前を変えた
  has(/<h2>KPIの推移 /, "推移グラフの見出しがKPIの推移になっている");
  if (/<h2>売上推移 /.test(src)) { console.log("  NG  古い見出し『売上推移』が残っています"); ng++; }
  else console.log("  OK  古い見出しは残っていない");

  // 広告の段の期間（ページ上部の期間とは独立）
  has(/data-apreset="thismonth"/, "広告の段に今月のボタン");
  has(/data-apreset="d7"/, "広告の段に7日間のボタン");
  has(/data-apreset="d28"/, "広告の段に28日間のボタン");
  has(/data-apreset="d90"/, "広告の段に3ヶ月のボタン");
  has(/if\(kind==="d7"\) return clampRange\(addDays\(today,-6\),today\);/, "7日間＝今日を含む7日");
  has(/if\(kind==="d28"\)return clampRange\(addDays\(today,-27\),today\);/, "28日間＝今日を含む28日");
  has(/const f=state\.aFrom\|\|state\.from, t=state\.aTo\|\|state\.to;/, "広告の段は自前の期間で集計する");
  has(/state\.aKind=b\.dataset\.apreset; state\.aFrom=f; state\.aTo=t;/, "期間ボタンが広告の段だけを描き直す");
  has(/on\("adfrom","onchange"/, "広告の段の開始日を直せる");
  has(/on\("adto","onchange"/, "広告の段の終了日を直せる");
  has(/\.achip\.on\{|,\.achip\.on\{/, "選んだ期間のボタンが点灯する（CSS）");
  has(/b\.dataset\.apreset===state\.aKind/, "選んだ期間のボタンが点灯する（描画）");
}

// --- AIのまとめ・予算の出所・レポート -------------------------------
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  // ① 週次に広告費と納品点数を載せてAIへ渡す
  has(/ad:sumIn\(adDayA,wf,wt\), buy:sumIn\(shipA,wf,wt\)/, "週次に広告費と納品点数を載せている");
  has(/ad=Meta広告費/, "凡例に広告費を書いている（列だけ渡して意味を伝えない事故を防ぐ）");
  has(/buy=納品点数/, "凡例に納品点数を書いている");
  has(/因果と決めつけず/, "AIに因果と決めつけないよう指示している");
  has(/function adBuyLine\(weekly\)/, "AIが使えないときの自動まとめにも広告・納品がある");
  has(/if\(Math\.abs\(exCh\)<10\)return "";/, "売上が動いていない週には触れない");
  // ④' 予算の出所
  has(/let BUDGET=\{ym:null,ex:null,src:null\}/, "予算に出所を持たせている");
  has(/if\(ex!=null\)src="事業計画"/, "事業計画から取れたことを記録する");
  has(/note\.textContent=`\$\{\+ym\.slice\(5,7\)\}月`\+\(src\?`・\$\{src\}`:""\)/, "予算カードに出所を出す");
  // ⑥ レポートの広告と納品
  has(/head\(sl,"広告と納品"/, "レポートに広告と納品のページがある");
  has(/if\(\(rAd&&rAd\.sp>0\)\|\|\(rSh&&rSh\.qty>0\)\)\{/, "どちらも無い店ではページごと出さない");
  has(/納品が空いた最長日数/, "納品の間隔が空いていないかを出す");
}

// --- 店舗ページの広告カードを選べること -------------------------------
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/const ST_AD_DEFAULT=\["sp","sr","ctr","frq"\];/, "既定は これまでの4つ（広告費・売上比率・CTR・FQ）");
  has(/localStorage\.setItem\("notime-stadcols"/, "選んだ項目を端末に覚える");
  // 全社を見る広告タブと、1店を見る店舗ページとで、見たい項目は違う。別のキーで持つ
  has(/"notime-adcols"/, "広告タブは notime-adcols で覚える");
  has(/"notime-stadcols"/, "店舗ページは notime-stadcols で覚える（別のキー）");
  has(/if\(i>=0\)\{ if\(ST_AD_COLS\.length<=1\)\{cb\.checked=true;return;\}/, "最後の1つは消せない（空のカード列を作らない）");
  has(/ST_AD_COLS=AD_METRICS\.map\(m=>m\.k\)\.filter/, "並びは指標の定義順にそろえる（押した順に散らばらせない）");
}

// --- 「表示する項目」はプルダウンで最大8つ ----------------------------
// チップを全部横に並べると、指標が増えるほどスマホで何行にもなり、
// 肝心のカードより目立ってしまう。畳んで、上限を決める。
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/const ST_AD_MAX=8;/, "選べるのは最大8つ");
  has(/function drawStoreAdPick\(\)/, "表示する項目はプルダウンで選ぶ");
  has(/const lock=!on&&n>=ST_AD_MAX;/, "8つ選んだら、それ以上は押せない");
  has(/if\(ST_AD_COLS\.length>=ST_AD_MAX\)\{cb\.checked=false;return;\}/, "上限を超えて増えない（チェックも戻す）");
  has(/\.slice\(0,ST_AD_MAX\)/, "端末に残った古い設定も上限まで切り詰める");
  has(/表示する項目（\$\{n\}\/\$\{ST_AD_MAX\}）/, "いま何個選んでいるかをボタンに出す");
  has(/let stAdPickOpen=false;/, "開いているかどうかを覚える");
  has(/drawStoreAds\(\);   \/\/ カードと注記を作り直す（プルダウンは開いたまま）/, "選び直してもプルダウンは開いたまま（続けて選べる）");
  has(/list\.onclick=e=>e\.stopPropagation\(\);/, "プルダウンの中を押しても閉じない");
  has(/document\.addEventListener\("click",\(\)=>\{ if\(stAdPickOpen\)/, "外側を押すと閉じる");
  has(/e\.key==="Escape"&&stAdPickOpen/, "Escでも閉じる");
  // 「横は4列のまま」が要件。カードの入れ物のクラスを勝手に変えていないこと
  has(/<div class="dtop four" id="stad-cards">/, "広告カードは4列のまま（8つ選ぶと2行×4列）");
  has(/\.dtop\.four\{grid-template-columns:repeat\(4,minmax\(0,1fr\)\)\}/, "4列の指定がCSSに残っている");
  // 店舗ページのチップは撤去した。全社を見る広告タブのチップ（.adcol／notime-adcols）は
  // そのまま残す＝ここで消えていてほしいのは #stad-pick の側だけ
  if (/pick\.querySelectorAll\("\.adcol"\)/.test(src)) {
    console.log("  NG  店舗ページに古いチップの並びが残っています"); ng++;
  } else console.log("  OK  店舗ページの古いチップは消えている");
  has(/document\.querySelectorAll\("\.adcol"\)/, "全社の広告タブのチップは残っている（別の画面なので触らない）");
  has(/adHas\(m\.need\)\?\(m\.note\|\|""\):"元データがまだDBにありません"/, "値が出せない指標は理由をカードに書く");

  // ---- 店舗ページでもカードをタップしてグラフに線を出せること ----
  // 広告タブと同じ操作にそろえる。グラフは作り直さず同じ adChart を使う
  // （別に作ると、正規化のしかたや軸の決め方がいつの間にか食い違う）
  has(/function adChart\(days,byDay,cols,tot\)/, "グラフは項目と合計を外から受け取れる");
  has(/function adWireTip\(days,byDay,cols,sel\)/, "吹き出しも項目と置き場所を外から受け取れる");
  has(/adChart\(days,byDay,gcols,p\)/, "店舗ページも同じ adChart を使う（作り直していない）");
  has(/adWireTip\(days,byDay,gcols,"#stad-trend"\)/, "店舗ページのグラフにも吹き出しが付く");
  has(/<div id="stad-trend"/, "店舗ページにグラフの置き場所がある");
  has(/class="card adcard\$\{off\?" goff":""\}" data-k="\$\{k\}"/, "店舗ページのカードはタップできる");
  // 全社の画面と1店の画面で、消した線が飛び火すると分かりにくい
  has(/"notime-stadgoff"/, "店舗ページの非表示は notime-stadgoff で覚える");
  has(/"notime-adgoff"/, "広告タブの非表示は notime-adgoff のまま（別のキー）");
  has(/let ST_AD_GOFF=/, "店舗ページの非表示は別に持つ");
  // 全部消すとグラフが空の箱になる。何をすれば戻るかを書いておく
  has(/グラフに出す項目がありません。上のカードをタップすると、その項目をグラフに出せます。/,
    "全部消したときは戻し方を書く");
  // 日ごとの売上は、その日その店の実額。店舗集合は使わない（店は1つに決まっている）
  has(/a\.sids=null; a\.ex=M\("ex"\)\.calc\(sumRows\(rowsIn\(d,d,id\)\)\)\|\|0;/,
    "日ごとの売上はその日・その店の実額で入れる");
  has(/\{k:"lkr", name:"いいね率"/, "いいね率がある");
  has(/\{k:"vt",  name:"平均視聴時間"/, "平均視聴時間がある");
  has(/calc:p=>p\.im>0\?p\.lk\/p\.im\*100:null/, "いいね率＝いいね数÷表示回数");
  // 平均視聴時間は Meta が返す値をそのまま使う。こちらで重み付けなどの加工はしない
  has(/calc:p=>p\.vn>0\?p\.vt\/p\.vn:null/, "平均視聴時間は値がある行の平均で出す");
  has(/if\(r\.vt!=null\)\{ a\.vt\+=r\.vt\|\|0; a\.vn\+\+; \}/, "視聴時間が無い行は数えない（0秒で薄めない）");
  has(/if\(r\.vt!=null\)\{ e\.vt\+=r\.vt\|\|0; e\.vn\+\+; \}/, "クリエイティブ別も同じ扱い");
  if (/p\.vw|e\.vw/.test(src)) {
    console.log("  NG  平均視聴時間に重み付けの計算が残っています"); ng++;
  } else console.log("  OK  平均視聴時間に余計な計算を入れていない");
}

// --- AIのまとめの見せ方 ------------------------------------------------
// 実物の aiFormat / aiNum を取り出して動かす（正規表現の書き間違いは目で見ても分からない）。
{
  const f2 = src.indexOf("const AI_UNIT=");
  const t2 = src.indexOf("// ---- AIのまとめ（ページ最下部のカード）----");
  if (f2 < 0 || t2 <= f2) {
    console.log("  NG  aiFormat を取り出せませんでした"); ng++;
  } else {
    const c2 = { console };
    vm.createContext(c2);
    // esc は画面側の実物と同じ振る舞い（HTMLの特殊文字を潰す）
    vm.runInContext(
      'function esc(s){return String(s==null?"":s)' +
      '.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}\n' +
      src.slice(f2, t2), c2);
    const fmt = (s) => vm.runInContext("aiFormat(" + JSON.stringify(s) + ")", c2);
    const ok = (label, cond) => {
      console.log(`  ${cond ? "OK " : "NG "} ${label}`);
      if (!cond) ng++;
    };
    ok("話題ごとに1行に割る（一かたまりで出さない）",
      (fmt("広告：あ\n来店：い").match(/class="ailine"/g) || []).length === 2);
    ok("空行は行として数えない",
      (fmt("あ\n\n\nい").match(/class="ailine"/g) || []).length === 2);
    ok("【】で囲まれたところが赤になる",
      /<b class="aihi">CTRが12%低い<\/b>/.test(fmt("広告：【CTRが12%低い】です。")));
    ok("【】は画面に残らない", !/[【】]/.test(fmt("広告：【CTRが12%低い】です。")));
    ok("見出しは太字にする", /<span class="ailbl">広告：<\/span>/.test(fmt("広告：あ")));
    ok("見出しが無い行でも壊れない",
      /class="ailine">売上が伸びました/.test(fmt("売上が伸びました")));
    // 数字を全部太字にするのは一度やってやめた。1行に十数個あるので、
    // 全部目立たせると結局どれも目立たない。色を付けるのは差・増減だけ
    ok("ただの実績値は装飾しない",
      !/<b/.test(fmt("CTRは12%でした")) && !/<b/.test(fmt("広告費は1,234,567円です")));
    // 「12」と「%」が別々に色付くと読みにくい。ひとかたまりで扱う
    ok("数字と単位はひとかたまりで扱う",
      /<b class="aihi">1,234,567円増<\/b>/.test(fmt("広告費は1,234,567円増です")));
    ok("符号付きの増減も拾う", /<b class="aihi">\+15%増<\/b>/.test(fmt("売上は+15%増です")));
    // 赤の中でさらに色を変えると、せっかくの「ここが大事」がぼやける
    ok("赤にしたところの中を二重に色付けしない",
      !/aihi">[^<]*<b/.test(fmt("【CTRが12%低い】")));
    // 増減の言い回しが付いた数字は、ただの実績値より優先して赤にする
    ok("赤の外にある増減も赤になる",
      /<b class="aihi">34%増<\/b>/.test(fmt("【CTRが12%低い】。来店は34%増でした")));
    // AIの答えはそのままHTMLに入れる。タグを書かれても実行させない
    ok("HTMLタグを書かれても素通ししない",
      !/<img/.test(fmt("<img src=x onerror=alert(1)>")) &&
      /&lt;img/.test(fmt("<img src=x onerror=alert(1)>")));
    ok("空の答えなら何も出さない", fmt("") === "" && fmt(null) === "");

    // ここからは「AIが指示どおりに返してこなかったとき」の備え。
    // 実際、1行1話題と頼んでも、ひとつづきの段落で返ってきて読み飛ばされていた。
    // 実物の答え（2026-08-12 いわき店）をそのまま入れて確かめる。
    const REAL =
      "来店客数が575人と上位3店平均842人より約27%少ないことが最大の課題で、購入率28.9%（上位平均23.5%）や" +
      "商品単価3786円（同3693円）はむしろ強いため、既存客への接客力は高いが集客不足が売上差" +
      "（97.3万円 対 119.1万円、約22万円減）の主因と考えられます。売れ筋構成ではランクA×プリントTシャツが" +
      "8.5%（ブランド平均5.4%）と強い一方、ランクC×TシャツやランクS×Tシャツはブランド平均で6.0%・2.9%" +
      "あるのに自店では取り扱いゼロで、価格帯の幅を広げる余地があります。週次では7/20〜7/26に売上87.3万円・" +
      "来店386人と突出しており、これがSALEや企画によるものであれば、その施策の再実施が有効な打ち手になります。" +
      "直近週（8/10〜8/16）はまだ1日分のデータのみのため、トレンド判断は保留が妥当です。";
    const real = fmt(REAL);
    const nLines = (real.match(/class="ailine"/g) || []).length;
    ok(`改行が1つも無い段落でも行に割る（${nLines}行になった）`, nLines >= 4);
    ok("【】が無くても差・増減は赤になる", /<b class="aihi">27%少ない<\/b>/.test(real));
    ok("『約22万円減』のような万円表記も拾う", /<b class="aihi">22万円減<\/b>/.test(real));
    ok("『386人と突出』のように間に助詞があっても拾う",
      /<b class="aihi">386人と突出<\/b>/.test(real));
    // 差でも増減でもないただの実績値は、赤ではなく黒の太字にとどめる
    ok("ただの実績値は赤にしない",
      /来店客数が575人と上位3店平均842人より約<b class="aihi">/.test(real));
    ok("赤の中を二重に色付けしない", !/aihi">[^<]*<b/.test(real));
    // 1行の色は数か所まで。多すぎると結局どれも目立たない
    ok("1行の赤は多くても3か所まで",
      real.split("</p>").every((l) => (l.match(/class="aihi"/g) || []).length <= 3));
    // 短い文が単独の行になると、かえって読みにくい
    ok("『〜です。』だけの短い行を作らない",
      !/class="ailine">[^<]{0,12}<\/p>/.test(real));
    ok("短い答えは無理に切らない",
      (fmt("来店が伸びました。広告は据え置きです。").match(/class="ailine"/g) || []).length === 1);
    ok("行の途中で文が切れない（最後は。で終わる）",
      real.split("</p>").filter(Boolean).every((s) => !s.includes("ailine") || /。<\/?/.test(s + "</p>")));
  }
  // キャッシュは答えの文章そのもの。HTMLを入れると、見せ方を直しても古い分だけ昔の形で残る
  if (/localStorage\.setItem\(key,html\)/.test(src)) {
    console.log("  NG  AIのまとめのキャッシュにHTMLを入れています"); ng++;
  } else console.log("  OK  AIのまとめのキャッシュは文章そのものを持つ");
  if (/localStorage\.setItem\(key,answer\)/.test(src) && /"aiWeekly2:"/.test(src)) {
    console.log("  OK  見せ方を変えたのでキャッシュのキーも変えている");
  } else { console.log("  NG  キャッシュのキーが古いままです"); ng++; }
  // AIが繋がらない日だけ画面の印象が変わらないよう、自動集計版も同じ形で返す
  if (/return out\.slice\(0,4\)\.join\("\\n"\);/.test(src)) {
    console.log("  OK  自動集計版も1行1話題で返す");
  } else { console.log("  NG  自動集計版が1かたまりのままです"); ng++; }
  if (/out\.innerHTML=\(auto\?aiFormat\(auto\)/.test(src)) {
    console.log("  OK  自動集計版も同じ見せ方に通している");
  } else { console.log("  NG  自動集計版が昔の見せ方のままです"); ng++; }
}

console.log(ng ? `\n❌ ${ng}件ずれています。` : "\n✅ すべて期待どおりです。");
process.exit(ng ? 1 : 0);
