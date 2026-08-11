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

console.log(ng ? `\n❌ ${ng}件ずれています。` : "\n✅ すべて期待どおりです。");
process.exit(ng ? 1 : 0);
