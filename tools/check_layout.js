/*
 * 店舗ページ上部の4枚（前日・今月・着地見込み・売上予算）が、
 * スマホ幅で「円」や「達成見込」を切り落としていないかを、実際のブラウザで測る。
 *
 *     node tools/check_layout.js
 *
 * 【なぜ要るのか】
 *   ここは2度はみ出した。CSSは目で読んでも「入るかどうか」が分からない。
 *   桁の大きい金額（2,995,602円）と「達成見込 119.8%」は、幅が足りないと
 *   静かに切れるだけで、エラーにも警告にもならない。
 *   dashboard.html の <style> と実際のカードの作りをそのまま使って、
 *   Chromium に並べさせて、はみ出しているかを測る。
 *
 *   Chromium が無い環境（CI など）では、測らずに飛ばす（落とさない）。
 */
const fs = require("fs");
const path = require("path");
const os = require("os");
const { execFileSync } = require("child_process");

const HTML = path.join(__dirname, "..", "web", "dashboard.html");
const src = fs.readFileSync(HTML, "utf8");

const CHROME = ["/opt/pw-browsers/chromium", "/usr/bin/chromium",
                "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
  .find((p) => { try { return fs.statSync(p).isFile(); } catch { return false; } });
if (!CHROME) {
  console.log("  --  Chromium が無いので画面の測定は飛ばします");
  process.exit(0);
}

const style = /<style>[\s\S]*?<\/style>/.exec(src);
if (!style) { console.error("❌ dashboard.html から <style> を取り出せませんでした。"); process.exit(1); }

// 実物と同じクラスの組み立て（dashboard.html の headline の段）。
// 数字は桁がいちばん大きくなる実例を入れる。
const card = (cls, k, v, note, extra) =>
  `<div class="card ${cls}"><div class="k">${k}</div>` +
  `<div class="val">${v}<small>円</small></div>` +
  `<div class="knote">${note}</div>${extra || ""}</div>`;
const row = `<div class="dtop headline four">
${card("", "前日の売上", "209,465", "税抜 8/12")}
${card("", "今月の売上", "1,154,960", "税抜 8/1〜8/12")}
${card("land", "着地見込み", "2,995,602", "当月の月末予測")}
${card("bud", "売上予算", "2,500,000", "税抜 8月・事業計画",
  `<div class="rate">達成見込 119.8%</div>`)}
</div>`;

const page = `<!doctype html><html><head><meta charset="utf-8">${style[0]}</head>
<body><div style="padding:0 16px">${row}</div><pre id="out"></pre><script>
window.addEventListener("load",()=>{
  const g=document.querySelector(".dtop.headline");
  const cards=[...g.children];
  const rows=new Set(cards.map(c=>Math.round(c.getBoundingClientRect().top))).size;
  // scrollWidth > clientWidth ＝ 枠に入りきらず切れている
  const clipped=cards.flatMap(c=>[...c.querySelectorAll(".val,.rate,.knote,.k")]
    .filter(e=>e.scrollWidth>e.clientWidth+1)
    .map(e=>e.className+"「"+e.textContent.trim()+"」"));
  document.getElementById("out").textContent=JSON.stringify({
    w:document.documentElement.clientWidth, rows, clipped});
});
<\/script></body></html>`;

const tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "avend-layout-")), "fx.html");
fs.writeFileSync(tmp, page);

const measure = (w) => {
  const dom = execFileSync(CHROME, [
    "--headless", "--no-sandbox", "--disable-gpu",
    `--window-size=${w},900`, "--virtual-time-budget=2000",
    "--dump-dom", "file://" + tmp,
  ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], maxBuffer: 32 << 20 });
  const m = /<pre id="out">([\s\S]*?)<\/pre>/.exec(dom);
  if (!m) throw new Error("測定結果を読めませんでした");
  return JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
};

let ng = 0;
const ok = (label, cond) => { console.log(`  ${cond ? "OK " : "NG "} ${label}`); if (!cond) ng++; };

// Chromium はウィンドウ幅を 500px 未満にできないので、スマホ幅の代表として 500 を使う。
// 実機（iPhone は 390〜430px）はこれより狭いが、狭いほど2列になりやすい側なので、
// 500px で2列かつ切れていなければ、実機でも切れない。
for (const w of [500, 620]) {
  const r = measure(w);
  ok(`幅${r.w}px：上の4枚は2行に折り返す（1行4列だと「円」が切れる）`, r.rows === 2);
  ok(`幅${r.w}px：切れている文字が無い`, r.clipped.length === 0);
  if (r.clipped.length) console.log("      切れているもの: " + r.clipped.join(" / "));
}
// PC幅では今までどおり横1行
for (const w of [900, 1200]) {
  const r = measure(w);
  ok(`幅${r.w}px：横1行のまま`, r.rows === 1);
  ok(`幅${r.w}px：切れている文字が無い`, r.clipped.length === 0);
  if (r.clipped.length) console.log("      切れているもの: " + r.clipped.join(" / "));
}

console.log(ng ? `\n❌ ${ng}件ずれています。` : "\n✅ 上部カードは切れていません。");
process.exit(ng ? 1 : 0);
