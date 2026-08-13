/*
 * 店舗ページ「広告（Meta）」の推移まわりを、実物のコードをブラウザで動かして確かめる。
 *
 *     node tools/check_ad_panel.js
 *
 * 【なぜ要るのか】
 *   ここは一度、日次のグラフが丸ごと空になる壊し方をした。
 *   adRoll は日次のとき「渡された入れ物をそのまま」返すのに、呼ぶ側がそれを
 *   clear() してから詰め直していた＝同じものを空にして、空をなめていた。
 *
 *   このとき手で書いた確認用のページは adChart を直接呼んでいたので、
 *   呼ぶ側の間違いを一切通らずに「動いた」ように見えていた。
 *   なので、ここでは dashboard.html の drawStoreAds の中身をそのまま切り出して
 *   本物のブラウザで動かし、SVGの線が引かれたか・表に行が出たかをDOMで数える。
 *
 *   Chromium が無い環境では、測らずに飛ばす（落とさない）。
 */
const fs = require("fs");
const path = require("path");
const os = require("os");
const { execFileSync } = require("child_process");

const HTML = path.join(__dirname, "..", "web", "dashboard.html");
const src = fs.readFileSync(HTML, "utf8");

const CHROME = ["/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell",
                "/opt/pw-browsers/chromium", "/usr/bin/chromium",
                "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
  .find((p) => { try { return fs.statSync(p).isFile(); } catch { return false; } });
if (!CHROME) {
  console.log("  --  Chromium が無いので広告パネルの動作確認は飛ばします");
  process.exit(0);
}
const IS_SHELL = CHROME.endsWith("headless_shell");

// ---- dashboard.html から実物を切り出す --------------------------------
const cut = (a, b) => {
  const i = src.indexOf(a), j = src.indexOf(b);
  if (i < 0 || j < 0 || j <= i) {
    console.error(`❌ 取り出せませんでした: ${a}`);
    console.error("   （dashboard.html の並びが変わった可能性があります）");
    process.exit(1);
  }
  return src.slice(i, j);
};
const real = [
  cut("const AD_COLOR={", "const AD_DEFAULT="),
  cut("const AD_METRICS=[", "function adMetrics()"),
  cut("function adZero()", "function adSales("),
  cut("function adVal(", "function renderAds()"),
  cut("function adChart(", "// 軸の目盛り用に短くする"),
  cut("function adShort(m,v)", "// グラフに触れたとき"),
  cut("function adWireTip(", "function drawAds()"),
  cut("function adGrain()", "// ---- 広告カードの「表示する項目」プルダウン ----"),
  // ここが本題：呼ぶ側（drawStoreAds の推移の段）をそのまま動かす
  "function drawPanel(){\n" +
  cut("  const tel=document.getElementById(\"stad-trend\");",
      "  const note=document.getElementById(\"stad-note\");") +
  "}\n",
].join("\n");

// ---- 足りないものを埋める（データと、画面のほかの部分）------------------
// 実データは要らない。作り物を入れて、実物の計算とDOM操作だけを通す。
const stubs = `
const WK=["日","月","火","水","木","金","土"];
const YEN=n=>Number(n).toLocaleString("ja-JP");
const NUM=YEN;
const F1=n=>Number(n).toFixed(1);
const F2=n=>Number(n).toFixed(2);
function esc(s){return String(s==null?"":s)
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function mondayOf(d){const t=new Date(d+"T00:00:00Z");
  t.setUTCDate(t.getUTCDate()-((t.getUTCDay()+6)%7));return t.toISOString().slice(0,10);}
function addDays(d,n){const t=new Date(d+"T00:00:00Z");
  t.setUTCDate(t.getUTCDate()+n);return t.toISOString().slice(0,10);}
function mdLabel(f,t){const g=s=>(+s.slice(5,7))+"/"+(+s.slice(8,10));
  return f===t?g(f):g(f)+"〜"+g(t);}
// adHas は実物が動く。1行でも値があれば「入っている」判定なので、
// 全項目に値を入れておく（「—」の出方そのものは check_ads_math.js の担当）
const DATA={meta:[{pv:1,fl:1,lk:1,vt:1}]};
// 店の売上・来店。日ごとに少しずつ違う値を返す
function sumRows(a){return a;}
function rowsIn(f,t,id){return {ex:120000,hasV:true,v:60};}
function M(k){return {calc:a=>a.ex, fmt:v=>v};}
let AD_COLS=[], AD_GOFF=new Set();
function adRows(){return [];}
function adSum(){return adZero();}
// 広告の行。TEST_FROM〜TEST_TO のうち、3日に1度だけ出稿を止めている
function adRowsOfStore(id,f,t){
  const out=[]; let d=f, i=0;
  while(d<=t){ if(i%7!==3)out.push({d:d,si:1,sp:1000+i*10,im:5000+i*20,ck:200+i,rc:4000,pv:150+i,vt:null});
    d=addDays(d,1); i++; }
  return out;
}
`;

// dashboard.html の <style> も入れる。測るだけなら要らないが、
// AVEND_KEEP=1 で残したページをそのまま開いて目で見られるようにしておく
const STYLE = (/<style>[\s\S]*?<\/style>/.exec(src) || [""])[0];
const page = (from, to, grain, cols, goff) => `<!doctype html><html><head><meta charset="utf-8">${STYLE}</head>
<body style="padding:10px;width:393px;box-sizing:border-box;background:var(--bg)"><div class="panel">
<div id="stad-trend"></div><p class="note" id="stad-grainwarn"></p>
<div class="tblwrap" style="margin-top:8px"><table class="kmat" id="stad-tbl"></table></div>
</div><pre id="out" style="display:none"></pre>
<script>
${stubs}
let ST_AD_COLS=${JSON.stringify(cols)};
let ST_AD_GOFF=new Set(${JSON.stringify(goff)});
const state={aGrain:"${grain}"};
const f="${from}", t="${to}", id=1;
const p=adZero(); p.sids=null;
${real}
try{
  for(const r of adRowsOfStore(id,f,t))adAdd(p,r);
  p.ex=120000;
  drawPanel();
  const svg=document.querySelector("#stad-trend svg");
  document.getElementById("out").textContent=JSON.stringify({
    svg:!!svg,
    paths:svg?svg.querySelectorAll("path").length:0,
    dots:svg?svg.querySelectorAll("circle").length:0,
    xlabels:svg?[...svg.querySelectorAll("text")].map(e=>e.textContent):[],
    legend:[...document.querySelectorAll("#stad-trend .adlegend span")].map(e=>e.textContent),
    warn:document.getElementById("stad-grainwarn").textContent,
    headers:[...document.querySelectorAll("#stad-tbl thead th")].map(e=>e.textContent),
    bodyRows:document.querySelectorAll("#stad-tbl tbody tr").length,
    firstRow:[...(document.querySelectorAll("#stad-tbl tbody tr")[0]||{cells:[]}).cells||[]].map(e=>e.textContent),
    secondRow:[...(document.querySelectorAll("#stad-tbl tbody tr")[1]||{cells:[]}).cells||[]].map(e=>e.textContent),
    msg:document.querySelector("#stad-trend p.note")?document.querySelector("#stad-trend p.note").textContent:null,
  });
}catch(e){ document.getElementById("out").textContent=JSON.stringify({error:String(e&&e.stack||e)}); }
<\/script></body></html>`;

let ng = 0;
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "avend-adpanel-"));
const run = (from, to, grain, cols, goff) => {
  const file = path.join(dir, `${grain}-${from}-${to}.html`);
  fs.writeFileSync(file, page(from, to, grain, cols, goff));
  const dom = execFileSync(CHROME, [
    ...(IS_SHELL ? [] : ["--headless"]), "--no-sandbox", "--disable-gpu",
    "--window-size=393,900", "--virtual-time-budget=2500",
    "--dump-dom", "file://" + file,
  ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], maxBuffer: 32 << 20 });
  const m = /<pre id="out"[^>]*>([\s\S]*?)<\/pre>/.exec(dom);
  if (!m) throw new Error("測定結果を読めませんでした");
  const r = JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                           .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));
  // 画面のコードが例外で止まった場合。ここで投げると1件目で全部止まって
  // 何が起きたか読めないので、NGとして出してから空の結果で続ける
  if (r.error) {
    console.log(`  NG  ${grain}：画面のコードがエラーで止まりました`);
    console.log("      " + String(r.error).split("\n").slice(0, 2).join(" / "));
    ng++;
    return { svg: false, paths: 0, dots: 0, xlabels: [], legend: [],
             warn: "", headers: [], bodyRows: 0, firstRow: [], secondRow: [], msg: null };
  }
  return r;
};

const ok = (label, cond, extra) => {
  console.log(`  ${cond ? "OK " : "NG "} ${label}`);
  if (!cond) { ng++; if (extra !== undefined) console.log("      " + JSON.stringify(extra)); }
};

const COLS = ["sp", "sr", "ctr", "cpc"];

// ---- ① 日次。ここが空になる壊し方をしたので、いちばん厚く見る ----------
{
  const r = run("2026-07-01", "2026-08-12", "day", COLS, []);
  ok("日次：グラフが描かれる", r.svg, r);
  ok(`日次：線が項目の数だけ引かれる（${r.paths}本）`, r.paths === COLS.length, r);
  ok(`日次：点が打たれている（${r.dots}個）`, r.dots > 100, r);
  ok("日次：「グラフに出す項目がありません」になっていない",
    !(r.msg || "").includes("項目がありません"), r.msg);
  ok(`日次：横軸が日付（${(r.xlabels || []).slice(-1)}）`,
    (r.xlabels || []).some((s) => /^\d+\/\d+$/.test(s)), r.xlabels);
  ok("日次：点が多いので注意は出さない", r.warn === "", r.warn);
  ok(`日次：表の行は 合計1 + 43日 = 44（${r.bodyRows}）`, r.bodyRows === 44, r);
  ok("表の1行目は期間の合計", (r.firstRow || [])[0] === "期間の合計", r.firstRow);
  ok(`表は新しいものが上（${(r.secondRow || [])[0]}）`,
    /8\/12/.test((r.secondRow || [])[0] || ""), r.secondRow);
  ok("表の見出しはカードの項目とそろう",
    JSON.stringify(r.headers) === JSON.stringify(["日", "広告費", "売上比率", "CTR", "CPC"]), r.headers);
}

// ---- ② 週次 ----------------------------------------------------------
{
  const r = run("2026-07-01", "2026-08-12", "week", COLS, []);
  ok("週次：グラフが描かれる", r.svg && r.paths === COLS.length, r);
  ok(`週次：7週ぶんに丸まる（${r.bodyRows - 1}週）`, r.bodyRows - 1 === 7, r);
  ok("週次：点が7個あるので注意は出さない", r.warn === "", r.warn);
  ok(`週次：表の見出しが「週」（${(r.headers || [])[0]}）`, (r.headers || [])[0] === "週", r.headers);
  ok(`週次：行は「〜の週」（${(r.secondRow || [])[0]}）`,
    /の週$/.test((r.secondRow || [])[0] || ""), r.secondRow);
}

// ---- ③ 刻みが粗すぎるとき（点が3個以下）------------------------------
{
  const r = run("2026-08-01", "2026-08-12", "month", COLS, []);
  ok(`月次で12日ぶん：点が1個（${r.bodyRows - 1}）`, r.bodyRows - 1 === 1, r);
  ok("点が少ないと注意が出る", /点が1個しかありません/.test(r.warn), r.warn);
  // 点1個ではグラフに線が引けず、縦に大きな余白が空くだけ。下の表のほうが役に立つ
  ok("点が1個ならグラフは出さない", !r.svg, r);
  ok("グラフを出していない理由も書く", /線が引けないのでグラフは出していません/.test(r.warn), r.warn);
  ok("どうすれば見えるかも書いてある", /日次/.test(r.warn) && /期間を長く/.test(r.warn), r.warn);
  // 月の途中までしか入っていないことが分かるように
  ok(`途中までの月は範囲を添える（${(r.secondRow || [])[0]}）`,
    /8\/1〜8\/12の分/.test((r.secondRow || [])[0] || ""), r.secondRow);
}

// ---- ④ カードで線を消しても、表からは消えない ------------------------
//   グラフは形を見るもの、表は数字を読むもの。役割が違うので連動させない。
{
  const r = run("2026-07-01", "2026-08-12", "week", COLS, ["ctr", "cpc"]);
  ok(`線は残り2本になる（${r.paths}本）`, r.paths === 2, r);
  ok("表の列はカードの項目のまま（4つ）",
    JSON.stringify(r.headers) === JSON.stringify(["週", "広告費", "売上比率", "CTR", "CPC"]), r.headers);
}

// ---- ⑤ 全部消したとき ------------------------------------------------
{
  const r = run("2026-07-01", "2026-08-12", "day", COLS, COLS);
  ok("全部消すと、戻し方が出る", /カードをタップ/.test(r.msg || ""), r.msg);
  ok("それでも表は出したまま（数字は読める）", r.bodyRows > 1, r);
}

if (process.env.AVEND_KEEP) console.log(`\n  使ったページ: ${dir}`);
console.log(ng ? `\n❌ ${ng}件ずれています。` : "\n✅ 広告パネルは期待どおり動いています。");
process.exit(ng ? 1 : 0);
