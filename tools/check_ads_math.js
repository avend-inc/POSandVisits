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
const from = src.indexOf("let AD_SALES=null;");
const to = src.indexOf("function adVal(");
if (from < 0 || to < 0 || to <= from) {
  console.error("❌ dashboard.html から集計部分を取り出せませんでした。");
  console.error("   （adSalesIdx / adZero / adAdd / adSum の並びが変わった可能性があります）");
  process.exit(1);
}
const code = src.slice(from, to);
for (const name of ["adSalesIdx", "adZero", "adAdd", "adSum"]) {
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
// AD_SALES は let 宣言なので ctx のプロパティにならない。中に入って消す。
const setDaily = (daily) => {
  ctx.DATA = { daily };
  vm.runInContext("AD_SALES=null;", ctx);
};

let ng = 0;
const eq = (label, got, want) => {
  const ok = Math.abs((got == null ? NaN : got) - want) < 1e-9 ||
             (got == null && want == null);
  console.log(`  ${ok ? "OK " : "NG "} ${label}: ${got}（期待 ${want}）`);
  if (!ok) ng++;
};

console.log("広告タブの集計チェック");

// 店舗1: 8/8 の税抜売上 100,000円・来店 50人。同じ日に広告が3本走っている。
setDaily([
  { s: 1, d: "2026-08-08", ex: 100000, v: 50 },
  { s: 1, d: "2026-08-09", ex: 200000, v: 80 },
  { s: 2, d: "2026-08-08", ex: 50000, v: 20 },
]);
const rows = [
  { d: "2026-08-08", si: 1, sp: 1000, im: 10, rc: 5, ck: 2 },
  { d: "2026-08-08", si: 1, sp: 2000, im: 10, rc: 5, ck: 2 },
  { d: "2026-08-08", si: 1, sp: 3000, im: 10, rc: 5, ck: 2 },
];
let tot = ctx.adSum(rows);
eq("同じ店・同じ日の広告3本ぶんの広告費は足す", tot.sp, 6000);
eq("売上は1回だけ数える（3倍にしない）", tot.ex, 100000);
eq("来店も1回だけ数える", tot.vis, 50);
eq("売上比率＝6,000÷100,000", ctx.SR(tot), 6);

// 日をまたぐ・店をまたぐと、それぞれの売上が足される
tot = ctx.adSum([
  { d: "2026-08-08", si: 1, sp: 1000 },
  { d: "2026-08-09", si: 1, sp: 1000 },
  { d: "2026-08-08", si: 2, sp: 1000 },
]);
eq("日と店をまたぐと売上は合算", tot.ex, 350000);
eq("来店も合算", tot.vis, 150);

// 未紐付け（si が無い）行は売上を持ってこられない → 比率は「—」
tot = ctx.adSum([{ d: "2026-08-08", si: null, sp: 5000 }]);
eq("未紐付けの広告費は数える", tot.sp, 5000);
eq("未紐付けは売上が付かない", tot.ex, 0);
eq("売上が無ければ売上比率は出さない", ctx.SR(tot), null);

// 売上が取れない日（デジテール日次のみ＝ex が null）は足さない
setDaily([{ s: 1, d: "2026-08-08", ex: null, v: 30 }]);
tot = ctx.adSum([{ d: "2026-08-08", si: 1, sp: 1000 }]);
eq("税抜売上が無い日は売上に足さない", tot.ex, 0);
eq("来店だけは取れるので数える", tot.vis, 30);

console.log(ng ? `\n❌ ${ng}件ずれています。` : "\n✅ すべて期待どおりです。");
process.exit(ng ? 1 : 0);
