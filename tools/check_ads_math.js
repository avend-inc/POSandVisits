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
      // グラフの既定（広告費＋プロフアクセス数）はここでは関係ないので空で置く
      function adGoffDefault(){ return new Set(); }
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
  // 既定は上限いっぱいの8つ。要らない人が外すほうが、要る人が毎回足すより手数が少ない
  has(/const ST_AD_DEFAULT=\["sp","sr","pv","ctr","cpc","frq","lkr","vt"\];/,
    "既定は8つ（上限いっぱい）");
  has(/localStorage\.setItem\("notime-stadcols2"/, "選んだ項目を端末に覚える");
  // 既定を4つ→8つに増やしたので、キーも変える。変えないと、一度でも開いた端末に
  // 4つの選択が残っていて、既定を変えても8つに戻らない
  has(/"notime-stadcols2"/, "既定を増やしたのでキーも変えている");
  if (/"notime-stadcols"[^2]/.test(src)) {
    console.log("  NG  古いキー notime-stadcols がまだ使われています"); ng++;
  } else console.log("  OK  古いキー notime-stadcols は使っていない");
  // 全社を見る広告タブと、1店を見る店舗ページとで、見たい項目は違う。別のキーで持つ
  has(/"notime-adcols2"/, "広告タブは notime-adcols2 で覚える（既定にプロフアクセス数を足したのでキーも変えた）");
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

  // ---- CPC は画面のどこでも「広告費÷プロフアクセス数」 ----
  // 以前はカードのCPC（プロフアクセス基準）と表の「クリック単価」（クリック基準）が
  // 同じ画面に並んでいて、どちらの数字を見ているのか分からなかった。
  // 分母は「プロフアクセス数＋リンク遷移数」。最適化目標が PROFILE_VISIT の
  // キャンペーンしか pv を返さず、リンク遷移の広告では分母が空のまま消化額だけ
  // 分子に乗ってCPCが跳ねるため（2026-08 の福井で 15円→66円）
  has(/function adDen\(p\)\{ return \(p\.pv\|\|0\)\+\(p\.lp\|\|0\); \}/,
    "CPCの分母＝プロフアクセス数＋リンク遷移数");
  has(/calc:p=>adDen\(p\)>0\?p\.sp\/adDen\(p\):null, need:\{any:\["pv","lp"\]\}/,
    "カードのCPCも同じ分母を使う");
  has(/<th>CTR<\/th><th>CPC<\/th><\/tr>/, "キャンペーン別の列名はCPC");
  has(/<th>CTR<\/th><th>CPC<\/th><th>FQ<\/th><\/tr>/, "クリエイティブ別の列名もCPC");
  has(/\$\{\(e\.dnn&&\(e\.pv\+e\.lp\)>0\)\?YEN\(Math\.round\(e\.sp\/\(e\.pv\+e\.lp\)\)\)\+"円":"—"\}/,
    "表のCPCも同じ分母（クリック数では割らない）");
  // プロフアクセス数が未取込の日を0として足すと、CPCが実際より安く出る
  has(/if\(r\.pv!=null\|\|r\.lp!=null\)\{ e\.pv\+=r\.pv\|\|0; e\.lp\+=r\.lp\|\|0; e\.dnn\+\+; \}/,
    "分母は値がある行だけ数える（0で薄めない）");
  if (/店舗ページ[\s\S]{0,400}クリック単価|<th>クリック単価<\/th>/.test(
      src.slice(src.indexOf("// ---- キャンペーン別 ----")))) {
    console.log("  NG  店舗ページの表に「クリック単価」が残っています"); ng++;
  } else console.log("  OK  店舗ページの表から「クリック単価」は消えている");

  // ---- 店舗ページでもカードをタップしてグラフに線を出せること ----
  // 広告タブと同じ操作にそろえる。グラフは作り直さず同じ adChart を使う
  // （別に作ると、正規化のしかたや軸の決め方がいつの間にか食い違う）
  has(/function adChart\(days,byDay,cols,tot,fmtX\)/, "グラフは項目・合計・横軸の見出しを外から受け取れる");
  has(/function adWireTip\(days,byDay,cols,sel,fmtD\)/, "吹き出しも外から受け取れる");
  has(/adChart\(days,byB,gcols,p,fmtX\)/, "店舗ページも同じ adChart を使う（作り直していない）");
  has(/adWireTip\(days,byB,gcols,"#stad-trend",fmtD\)/, "店舗ページのグラフにも吹き出しが付く");
  // adRoll は日次のとき、渡された入れ物をそのまま返す。それを消してから詰め直すと
  // 「同じものを空にして、空をなめる」ことになり、日次のグラフが丸ごと消える。
  // 実際に一度やってしまったので、二度と書けないように見張る
  {
    const fn = src.slice(src.indexOf("// ---- 推移（この店だけ）----"),
                         src.indexOf("// ---- 刻みが粗すぎるときの注意 ----"));
    if (/byDay\.clear\(\)/.test(fn)) {
      console.log("  NG  adRoll に渡した入れ物を消しています（日次のグラフが空になります）"); ng++;
    } else console.log("  OK  adRoll が返した入れ物をそのまま使う（日次でも空にならない）");
  }
  // ---- 刻みが粗すぎるときの注意 ----
  has(/g!=="day"&&days\.length&&days\.length<=3/, "点が3個以下なら注意を出す");
  has(/形を見るには「日次」にするか、上の期間を長くしてください。/, "どうすれば見えるかを書く");
  // 日次で点が少ないのは期間が短いだけで、刻みのせいではない
  has(/const gw=document\.getElementById\("stad-grainwarn"\);/, "注意書きの置き場所がある");
  // ---- 刻みごとの数値の表 ----
  has(/<table class="kmat" id="stad-tbl">/, "グラフの下に数値の表がある");
  has(/const totRow=`<tr class="prow"><td>期間の合計<\/td>/, "表の1行目は期間の合計");
  has(/days\.slice\(\)\.reverse\(\)/, "表は新しいものが上");
  has(/if\(!adHas\(m\.need\)\)return `<td>—<\/td>`;/, "元データが無い項目は表でも「—」");
  has(/return `<td>\$\{v==null\?"—":m\.fmt\(v\)\}<\/td>`;/, "分母が0の比率も「—」（0にしない）");
  has(/<div id="stad-trend"/, "店舗ページにグラフの置き場所がある");
  has(/class="card adcard\$\{off\?" goff":""\}" data-k="\$\{k\}"/, "店舗ページのカードはタップできる");
  // 全社の画面と1店の画面で、消した線が飛び火すると分かりにくい
  has(/"notime-stadgoff2"/, "店舗ページの非表示は notime-stadgoff2 で覚える");
  has(/"notime-adgoff2"/, "広告タブの非表示は notime-adgoff2（別のキー）");

  // ---- グラフに最初から引くのは2本だけ ----
  // カードは8枚出すが、線を8本引くと重なって何も読めない（実際そうなっていた）
  has(/const AD_GRAPH_DEFAULT=\["sp","pv"\];/, "最初から引くのは 広告費 と プロフアクセス数");
  has(/const adGoffDefault=\(\)=>new Set\(AD_METRICS\.map\(m=>m\.k\)\.filter\(k=>!AD_GRAPH_DEFAULT\.includes\(k\)\)\)/,
    "それ以外は最初は消しておく（押せば出る）");
  has(/return adGoffDefault\(\);\n\}\)\(\);/, "広告タブの既定に使う");
  // カードに無い項目は線にできないので、既定のカードにも入れておく
  has(/const AD_DEFAULT=\["sp","pv","sr","ctr","cpc"\];/, "広告タブの既定カードにプロフアクセス数を入れる");
  // 既定を変えたときはキーも変える。変えないと、一度でも開いた端末に古い既定が残る
  {
    const olds = ['"notime-adgoff"', '"notime-stadgoff"', '"notime-adcols"'];
    const left = olds.filter((k) => new RegExp(k.replace(/"/g, '"') + "[^2]").test(src));
    if (left.length) {
      console.log("  NG  古いキーが残っています: " + left.join(", ")); ng++;
    } else console.log("  OK  古いキーは使っていない（既定の変更が効く）");
  }

  // ---- 押した期間を画面ごとに覚える ----
  has(/function adPeriodSave\(key,kind,from,to\)/, "期間を覚える道具がある");
  has(/function adPeriodRestore\(key\)/, "覚えた期間を戻す道具がある");
  // 「今月」は意味で覚える。日付をそのまま覚えると、翌月に開いても先月のままになる
  has(/if\(v\.kind&&v\.kind!=="custom"\)\{\n\s*const r=presetRange\(v\.kind\);/,
    "プリセットは意味で覚える（次に開くとそのときの今月になる）");
  has(/if\(v\.from&&v\.to\)return \[clampLo\(v\.from\),clampHi\(v\.to\),"custom"\];/,
    "日付を直接いじったときだけ、その日付を覚える");
  // 画面ごとに別のキー。共有すると片方を変えたらもう片方も変わる
  has(/"notime-adtab-period"/, "広告タブは自分のキーで覚える");
  has(/"notime-stad-period"/, "店舗ページの広告は自分のキーで覚える");
  has(/adPeriodSave\("notime-stad-period",state\.aKind,f,t\);/, "店舗ページの期間ボタンを覚える");
  has(/const r=adPeriodRestore\("notime-adtab-period"\);/, "広告タブは開いたときに覚えた期間を使う");
  has(/const r=adPeriodRestore\("notime-stad-period"\)\|\|presetRange\("d28"\)\.concat\("d28"\);/,
    "店舗ページも同じ（無ければ直近28日）");
  has(/let ST_AD_GOFF=/, "店舗ページの非表示は別に持つ");
  // 全部消すとグラフが空の箱になる。何をすれば戻るかを書いておく
  has(/グラフに出す項目がありません。上のカードをタップすると、その項目をグラフに出せます。/,
    "全部消したときは戻し方を書く");
  // 日ごとの売上は、その日その店の実額。店舗集合は使わない（店は1つに決まっている）
  has(/a\.sids=null; a\.ex=M\("ex"\)\.calc\(sumRows\(rowsIn\(d,d,id\)\)\)\|\|0;/,
    "日ごとの売上はその日・その店の実額で入れる");

  // ---- グラフの刻み（日次／週次／月次） ----
  // 上の段＝いつを見るか（期間）、この段＝どう刻むか。役割が違うので見た目も変える
  has(/<div class="seg agrain">/, "刻みは「ひとかたまりの切り替え」で出す（丸いチップの2行目にしない）");
  has(/<button data-agrain="week">週次<\/button>/, "週次がある");
  has(/<button data-agrain="month">月次<\/button>/, "月次がある");
  has(/function adRoll\(byDay,grain\)/, "日ごとの集計を週・月にまとめ直す関数がある");
  has(/state\.aGrain=b\.dataset\.agrain;/, "押すと刻みが変わる");
  has(/"notime-adgrain"/, "選んだ刻みは端末に覚える");
  // 期間と刻みは別もの。期間を押したときに刻みを戻してしまうと、
  // 「3ヶ月を週次で見る」が毎回やり直しになる
  {
    const fn = src.slice(src.indexOf('document.querySelectorAll(".achip").forEach'),
                         src.indexOf('// グラフの刻み（日次/週次/月次）'));
    if (/aGrain/.test(fn)) {
      console.log("  NG  期間を押すと刻みまで戻ってしまいます"); ng++;
    } else console.log("  OK  期間を変えても刻みは保つ");
  }
}

// --- 週次・月次にまとめても、比率が「比率の平均」にならないこと ----------
//   ここが壊れやすい。日ごとのCTRを平均すると、広告費の少ない日が同じ重みで
//   効いてしまい、実際とは別の数字になる。実額を足してから割ること。
{
  const f3 = src.indexOf("function adRoll(byDay,grain)");
  const t3 = src.indexOf("// ---- 広告カードの「表示する項目」プルダウン ----");
  if (f3 < 0 || t3 <= f3) { console.log("  NG  adRoll を取り出せませんでした"); ng++; }
  else {
    const c3 = { console, state: {} };
    vm.createContext(c3);
    vm.runInContext(
      "const WK=['日','月','火','水','木','金','土'];\n" +
      "function mondayOf(d){const t=new Date(d+'T00:00:00Z');" +
      "t.setUTCDate(t.getUTCDate()-((t.getUTCDay()+6)%7));return t.toISOString().slice(0,10);}\n" +
      "function mdLabel(f,t){const g=s=>`${+s.slice(5,7)}/${+s.slice(8,10)}`;" +
      "return f===t?g(f):`${g(f)}〜${g(t)}`;}\n" +
      "function addDays(d,n){const t=new Date(d+'T00:00:00Z');" +
      "t.setUTCDate(t.getUTCDate()+n);return t.toISOString().slice(0,10);}\n" +
      "function adZero(){ return {sp:0,im:0,rc:0,ck:0,fl:0,pv:0,lk:0,vt:0,vn:0,ex:0,vis:0,sids:null}; }\n" +
      src.slice(f3, t3), c3);
    // 8/3(月)〜8/9(日) の1週間。広告費もクリックも日によって大きく違う
    const days = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
                  "2026-08-07", "2026-08-08", "2026-08-09"];
    const mk = `
      const byDay=new Map();
      const D=${JSON.stringify(days)};
      // 8/3 は広告費9,000円・クリック90（CTR 1%）、他の6日は100円・クリック10（CTR 10%）
      D.forEach((d,i)=>{const a=adZero();
        a.sp=i===0?9000:100; a.im=i===0?9000:100; a.ck=i===0?90:10; a.pv=i===0?900:10;
        a.ex=100000; byDay.set(d,a);});
      const r=adRoll(byDay,"week");
      JSON.stringify({keys:r.keys, sp:[...r.map.values()][0].sp, im:[...r.map.values()][0].im,
        ck:[...r.map.values()][0].ck, pv:[...r.map.values()][0].pv,
        x:r.fmtX(r.keys[0]), d:r.fmtD(r.keys[0])})`;
    const r = JSON.parse(vm.runInContext(mk, c3));
    const okc = (label, cond) => { console.log(`  ${cond ? "OK " : "NG "} ${label}`); if (!cond) ng++; };
    okc("週次は月曜はじまりの1本にまとまる", r.keys.length === 1 && r.keys[0] === "2026-08-03");
    okc(`広告費は実額の合計（${r.sp}円）`, r.sp === 9600);
    okc(`クリックも実額の合計（${r.ck}）`, r.ck === 150);
    // 実額÷実額なら 150/9600=1.56%。日ごとのCTRを平均すると (1+10*6)/7=8.7% になる
    okc("CTRは 実額÷実額 になる（日ごとの比率を平均していない）",
      Math.abs(r.ck / r.im * 100 - 1.5625) < 1e-9);
    okc("CPCも 実額÷実額（9,600÷960=10円）", Math.abs(r.sp / r.pv - 10) < 1e-9);
    okc(`横軸は週はじめの日付（${r.x}）`, r.x === "8/3");
    okc(`吹き出しは週の範囲（${r.d}）`, /8\/3〜8\/9の週/.test(r.d));
    // 月次
    const m = JSON.parse(vm.runInContext(`
      const b2=new Map();
      b2.set("2026-07-31",Object.assign(adZero(),{sp:500,ex:1000}));
      b2.set("2026-08-01",Object.assign(adZero(),{sp:100,ex:1000}));
      b2.set("2026-08-12",Object.assign(adZero(),{sp:200,ex:1000}));
      const r2=adRoll(b2,"month");
      JSON.stringify({keys:r2.keys, aug:r2.map.get("2026-08-01").sp,
        x:r2.fmtX("2026-08-01"), d:r2.fmtD("2026-08-01")})`, c3));
    okc("月次は月ごとに1本", m.keys.length === 2);
    okc(`同じ月の広告費はまとまる（${m.aug}円）`, m.aug === 300);
    okc(`横軸は「8月」（${m.x}）`, m.x === "8月");
    // 期間の端は月の途中までしか入っていない。「8月」とだけ出すと1ヶ月ぶんに見える
    okc(`途中までの月は入っている範囲を添える（${m.d}）`, m.d === "2026年8月（8/1〜8/12の分）");
    const full = vm.runInContext(`
      const b4=new Map();
      b4.set("2026-08-01",adZero()); b4.set("2026-08-31",adZero());
      adRoll(b4,"month").fmtD("2026-08-01")`, c3);
    okc(`月まるごとなら「2026年8月」だけ（${full}）`, full === "2026年8月");
    // 日次のときは何も変えない（同じ入れ物をそのまま返す）
    const d1 = vm.runInContext(`
      const b3=new Map([["2026-08-03",adZero()]]);
      const r3=adRoll(b3,"day"); (r3.map===b3)+"|"+r3.fmtX("2026-08-03")`, c3);
    okc("日次は日ごとのまま（まとめ直さない）", d1.startsWith("true|"));
    okc("日次の横軸は「8/3」", d1.endsWith("|8/3"));
  }
}

// --- 指標の定義（いいね率・平均視聴時間）------------------------------
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/\{k:"lkr", name:"いいね率"/, "いいね率がある");
  has(/\{k:"vt",  name:"平均視聴時間"/, "平均視聴時間がある");
  has(/calc:p=>p\.im>0\?p\.lk\/p\.im\*100:null/, "いいね率＝いいね数÷表示回数");
  // 平均視聴時間は「1再生あたりの秒数」なので、行をまたいで単純平均すると
  // 10回しか再生されなかった広告と1万回再生された広告が同じ重みになる。
  // Σ(秒数×再生数)÷Σ再生数 にする。再生数が来ていない行しか無いときだけ単純平均。
  has(/calc:p=>p\.vp>0\?p\.vw\/p\.vp:\(p\.vn>0\?p\.vt\/p\.vn:null\)/,
    "平均視聴時間は再生数で重み付けする");
  has(/if\(r\.vp>0\)\{ a\.vw\+=\(r\.vt\|\|0\)\*r\.vp; a\.vp\+=r\.vp; \}/,
    "重み付けは Σ(秒数×再生数) と Σ再生数 で持つ");
  has(/a\.vt\+=r\.vt\|\|0; a\.vn\+\+;/, "再生数が無い行のために単純平均も残す");
  has(/val:e=>e\.vp>0\?e\.vw\/e\.vp:\(e\.vn>0\?e\.vt\/e\.vn:null\)/, "クリエイティブ別も同じ扱い");
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

// --- 広告ページの階層（直営／FC／全社）--------------------------------
//   店舗に紐づく数字は区分で割れる。クリエイティブは1本の広告が複数店に
//   またがるので割れない（割ると二重に数えるか、広告費の按分が要る）。
//   割れないものは全社のページにしか置かない、という切り分けを見張る。
{
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/function adSeg\(\)/, "見ている区分を1か所で決めている");
  has(/function adSegOk\(r\)/, "行がその区分に入るかの判定が1か所にある");
  has(/function adOwnOf\(r\)/, "区分そのものを出す関数がある");
  has(/!AD_HIDE\.has\(r\.st\|\|UNMAPPED\)&&adSegOk\(r\)/, "集計の元が区分で絞られる");
  has(/for\(const r of \(DATA\.meta\|\|\[\]\)\)if\(adSegOk\(r\)\)set\.add/,
    "店舗プルダウンもその区分の店だけ");
  // 見出し（直営店／FC店／区分なし）の振り分けも同じ判定を通すこと。
  // ここが焼き付けの r.ow のままだと、FCページの中に「直営店」の見出しが出る
  has(/const ow=adOwnOf\(r\);/, "KPI表の見出しも同じ判定を通す");
  if (/e=\{own:r\.ow/.test(src)) {
    console.log("  NG  焼き付けの r.ow で区分を決めている箇所が残っています"); ng++;
  } else console.log("  OK  焼き付けの r.ow で区分を決めている箇所は無い");
  // URL が分かれた別ページであること（共有・再読み込みで同じ場所に戻る）
  has(/href="index\.html\?view=ads&g=own"/, "直営は別URL");
  has(/href="index\.html\?view=ads&g=fc"/, "FCは別URL");
  has(/id="tab-ads"/, "広告タブがある");
  // 広告は店舗のキャンペーンの中で走るので、1本の広告は必ず1店に属する。
  // だからクリエイティブも区分で割れる＝全社だけのページは要らない
  has(/<h2>クリエイティブ別 <span class="sub"[^>]*>（\$\{adSegLabel\(\)\}・1行＝1店の1広告）/,
    "クリエイティブ別も区分ごとに出す");
  {
    const fn = src.slice(src.indexOf("function adDrawCreatives(shortName)"),
                         src.indexOf("// 未紐付けキャンペーンの割り当て"));
    if (!/adSegOk\(r\)/.test(fn)) {
      console.log("  NG  クリエイティブ別に区分の絞り込みが掛かっていません"); ng++;
    } else console.log("  OK  クリエイティブ別も区分で絞る");
    // まとめる単位は「店舗 × 広告名」。広告名だけでまとめると、別々の店の
    // 同じ名前の広告が1行に合算され、広告費もCTRも店をまたいで混ざる
    if (/let e=g\.get\(r\.an\)/.test(fn)) {
      console.log("  NG  広告名だけでまとめています（別の店の同名広告が合算されます）"); ng++;
    } else console.log("  OK  広告名だけではまとめていない");
    if (/const key=\(r\.si==null\?"\?":r\.si\)\+"\\u001f"\+r\.an;/.test(fn)) {
      console.log("  OK  まとめる単位は 店舗 × 広告名（区切りはエスケープで書く）");
    } else { console.log("  NG  まとめる単位が 店舗 × 広告名 になっていません"); ng++; }
    if (/他\$\{sts\.length-1\}店/.test(fn)) {
      console.log("  NG  「他◯店」の表示が残っています（1行＝1店のはず）"); ng++;
    } else console.log("  OK  「他◯店」は出ない（1行＝1店の1広告）");
  }
  has(/return adSeg\(\)==="own" \? ow==="直営" : ow==="FC";/,
    "区分が決まらない行は直営にもFCにも入れない");
  // 区分が未設定の店は黙って直営に入れず、名前を出して直せるようにする
  has(/区分（直営／FC）が未設定の店が/, "区分が未設定の店を画面で知らせる");
  has(/店舗マスタの ownership を入れると/, "どう直せばよいかを書く");
}

// --- 広告画面で、区分の絞り込みを掛け忘れている箇所が無いか --------------
//   月次KPIだけ adRowsAll() を通らず DATA.meta を直に読んでいて、絞り込みを
//   掛け忘れていた（直営の月次KPIにFC店が出た）。同じ抜け方を二度としないよう、
//   広告画面の中の「生の DATA.meta 読み」を1つずつ数え上げて確かめる。
{
  const f5 = src.indexOf("function renderAds()");
  const t5 = src.indexOf("// 未紐付けキャンペーンの割り当て");
  if (f5 < 0 || t5 <= f5) { console.log("  NG  広告画面を取り出せませんでした"); ng++; }
  else {
    // 絞り込みが要らないもの＝数字ではないもの。ここに挙げた形だけを許す
    const OKAY = [
      { re: /reduce\(\(a,r\)=>r\.d>a\?r\.d:a/, why: "いちばん新しい日付を探すだけ" },
      { re: /if\(r\.d<state\.from\|\|r\.d>state\.to\|\|!\(r\.sp>0\)\)continue;/,
        why: "区分が未設定の店を探す（全区分を見るのが目的）" },
      { re: /const nomap=adSum\(/,
        why: "未紐付けの広告費を数える（区分に入らないものを出すのが目的）" },
    ];
    const lines = src.slice(f5, t5).split("\n");
    const bad = [];
    lines.forEach((l, i) => {
      if (!/\(DATA\.meta\|\|\[\]\)/.test(l)) return;
      const near = lines.slice(i, i + 4).join("\n");
      if (/adSegOk\(/.test(near)) return;                 // 絞り込み済み
      if (OKAY.some((o) => o.re.test(near))) return;      // 数字ではない
      bad.push((i + 1) + ": " + l.trim().slice(0, 70));
    });
    if (bad.length) {
      console.log(`  NG  区分の絞り込みが掛かっていない DATA.meta 読みが ${bad.length}箇所あります`);
      bad.forEach((b) => console.log("      " + b));
      ng++;
    } else console.log("  OK  広告画面の DATA.meta 読みは、すべて絞り込み済みか数字ではない");
  }
  // 月次KPIは自前で3か月ぶんを集めるので、いちばん抜けやすい。名指しで見張る
  const has = (re, label) => {
    if (re.test(src)) console.log(`  OK  ${label}`);
    else { console.log(`  NG  ${label}`); ng++; }
  };
  has(/const mrows=\(DATA\.meta\|\|\[\]\)\.filter\(r=>yms\.includes\(r\.d\.slice\(0,7\)\)[\s\S]{0,80}?adSegOk\(r\)\)/,
    "月次KPIも区分で絞る");
  has(/adBuildTable\(document\.getElementById\("adtbl"\), adRowsAll\(\)/, "週次KPIは adRowsAll を通る");
  // 区分を切り替えたとき、前の区分で選んでいた店の絞り込みが残ると、画面が丸ごと空になる
  has(/if\(AD_STORE&&!adStoreList\(\)\.includes\(AD_STORE\)\)\{/,
    "区分に無い店の絞り込みは「すべての店舗」に戻す");
  // 全社のページを消したので、区分に入らないもの（未紐付け）の広告費が
  // どこにも出なくなる恐れがある。消えていないことを見張る
  if (/const nomap=adSum\(adRowsAll\(\)/.test(src)) {
    console.log("  NG  未紐付けの広告費を adRowsAll から数えています（必ず0になります）"); ng++;
  } else console.log("  OK  未紐付けの広告費は区分を通さずに数える（0にならない）");
  has(/const nomap=adSum\(\(DATA\.meta\|\|\[\]\)\.filter\(r=>r\.d>=state\.from/,
    "未紐付けの広告費は生の行から数える");
  has(/直営・FCどちらの集計にも入っていません（下の「未紐付けキャンペーン」で店舗に割り当てると入ります）/,
    "入っていないことと、直し方を書く");
  // 未紐付けの割り当てパネルは、全社を消した以上どちらのページにも要る
  has(/<div class="panel" id="admappanel"/, "未紐付けキャンペーンのパネルがある");
  if (/\$\{adSeg\(\)!=="all"\?/.test(src)) {
    console.log("  NG  全社だけに出す分岐が残っています"); ng++;
  } else console.log("  OK  全社だけに出す分岐は残っていない");
}

// --- 区分の判定を実際に動かす ------------------------------------------
//   ここは「直営にFC店が出る／その逆」で一度やらかした。焼き付けの r.ow ではなく
//   店舗マスタを見ること、空欄を直営に潰さないことを、実物を動かして確かめる。
{
  const f4 = src.indexOf("function adSeg()");
  const t4 = src.indexOf("// 期間内に広告費が動いた店舗");
  if (f4 < 0 || t4 <= f4) { console.log("  NG  adSegOk を取り出せませんでした"); ng++; }
  else {
    const c4 = { console, GROUP: "own" };
    vm.createContext(c4);
    vm.runInContext(
      // 店舗マスタ：1=直営 / 2=FC / 3=ownership が空 / 4=マスタに無い
      "const STORES={1:{id:1,own:'直営'},2:{id:2,own:'FC'},3:{id:3,own:null}};\n" +
      "function storeMeta(sid){return STORES[sid]||null;}\n" +
      "function ownOf(sid){const s=storeMeta(sid);return (s&&s.own)?s.own:'直営';}\n" +
      src.slice(f4, t4), c4);
    const seg = (g, r) => { c4.GROUP = g; return vm.runInContext("adSegOk(" + JSON.stringify(r) + ")", c4); };
    const okc = (label, cond) => { console.log(`  ${cond ? "OK " : "NG "} ${label}`); if (!cond) ng++; };

    // 焼き付け(ow)と店舗マスタが食い違うケース。マスタを正とする
    okc("マスタがFCなら、焼き付けが直営でもFC側に入る",
      seg("fc", { si: 2, ow: "直営" }) === true && seg("own", { si: 2, ow: "直営" }) === false);
    okc("マスタが直営なら、焼き付けがFCでも直営側に入る",
      seg("own", { si: 1, ow: "FC" }) === true && seg("fc", { si: 1, ow: "FC" }) === false);
    // 素直なケース
    okc("直営の店は直営だけに出る",
      seg("own", { si: 1, ow: "直営" }) === true && seg("fc", { si: 1, ow: "直営" }) === false);
    okc("FCの店はFCだけに出る",
      seg("fc", { si: 2, ow: "FC" }) === true && seg("own", { si: 2, ow: "FC" }) === false);
    // ownership が空の店。黙って直営に入れない
    okc("区分が空の店は直営にもFCにも入らない",
      seg("own", { si: 3 }) === false && seg("fc", { si: 3 }) === false);
    // 区分が空の店・未紐付けは、どちらの集計にも入れない（決められないため）。
    // 消えたことが分からないと困るので、画面で名指しして知らせる作りにしてある
    // 店舗に紐づいていない行（未紐付け）
    okc("未紐付けは直営にもFCにも入らない",
      seg("own", { si: null, ow: null }) === false && seg("fc", { si: null, ow: null }) === false);

    // どの区分でも、行がどこかに必ず1回は出る（＝合計が消えない・二重にならない）
    const rows = [{ si: 1 }, { si: 2 }, { si: 3 }, { si: null }];
    const n = rows.filter((r) => seg("own", r)).length + rows.filter((r) => seg("fc", r)).length;
    okc(`直営とFCで二重に数えない（${n}件／全4件のうち区分がつくのは2件）`, n === 2);
  }
}

// --- 取り込み待ちの言い回し -------------------------------------------
//   sql/030 で列は足した。「列が無い」と書いたままだと、SQLを流したのに
//   直っていないように読める。
{
  if (/まだDBに列が無いため/.test(src)) {
    console.log("  NG  「列が無い」のままの注記が残っています（列は用意済み）"); ng++;
  } else console.log("  OK  取り込み待ちだと書いている（列が無い、ではない）");
}

console.log(ng ? `\n❌ ${ng}件ずれています。` : "\n✅ すべて期待どおりです。");
process.exit(ng ? 1 : 0);
