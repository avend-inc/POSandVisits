/*
 * 画面を実際にブラウザで開いて、実行時エラーが出ないかを見る。
 *
 *     node tools/check_smoke.js
 *
 * 【なぜ要るのか】
 *   これまでの確認は「関数を切り出して動かす」もので、呼ぶ側の間違いや、
 *   画面を組み立てる途中で落ちる類の壊れ方は通り抜けていた（実際に、日次の
 *   グラフが丸ごと空になる不具合を1度通してしまった）。
 *
 *   dashboard.html には window.__DATA__ にデータを入れておくと通信せずに
 *   そのまま描く経路がある。そこへ作り物のデータを流し込み、各画面を開いて
 *   ・例外が出ないか（window.onerror / unhandledrejection）
 *   ・console.error が出ていないか
 *   ・中身が実際に描かれたか（空の箱になっていないか）
 *   を見る。実データは要らないので、誰の環境でも同じ結果になる。
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
  console.log("  --  Chromium が無いので画面の動作確認は飛ばします");
  process.exit(0);
}
const IS_SHELL = CHROME.endsWith("headless_shell");

// ---- 作り物のデータ ----------------------------------------------------
// 実データは使わない。壊れ方を見つけたいのはコードのほうなので、
// 「よくある形」と「端のケース」を混ぜて作る。
const DAYS = 420;                      // 年をまたぐ長さ（「全期間」の見え方を見るため）
// 最終日が「今」に近くなるように、DAYS ぶんさかのぼった日から並べる
const day = (n) => {
  const t = new Date(Date.UTC(2026, 7, 5) - (DAYS - 1 - n) * 86400000);
  return t.toISOString().slice(0, 10);
};
const STORES = [
  { id: 1, name: "NTMいわき", own: "直営", kv: true, visible: true },
  { id: 2, name: "NTM所沢", own: "FC", kv: true, visible: true },
  { id: 3, name: "NTM区分なし", own: null, kv: true, visible: true },   // ownership 未設定
  { id: 4, name: "NTM来店なし", own: "直営", kv: false, visible: true }, // 来店をKPIに使わない
  // 1店で2レジ（無人＝SIPOS／有人＝Airレジ）を使い分ける店＝下北沢。
  // 内訳の段・有人来店の手入力・来店数の合算という、この店だけの作りがある。
  // 広告を全店に広げたときに、そこが消えないことを見る
  { id: 5, name: "SFG下北沢", own: "直営", kv: true, visible: true, mix: true },
  // あとから開いた店。「全期間」がその店の範囲になることを見る
  //（全店の最古から始めると、左半分が空欄になって推移が右端に潰れる）
  { id: 6, name: "NTM新店", own: "直営", kv: true, visible: true, opensAt: 300 },
];
const LATE = 6, LATE_OPENS = 300;
const SHIMO = 5;
const daily = [];
for (let i = 0; i < DAYS; i++) {
  for (const s of STORES) {
    if (s.opensAt != null && i < s.opensAt) continue;   // 開店前は行そのものが無い
    const r = {
      d: day(i), s: s.id,
      ex: (s.id === 4 && i % 7 === 0) ? null : 80000 + ((i * 37 + s.id * 11) % 40000),
      in: 90000, tx: 20 + (i % 9), it: 40 + (i % 5),
      v: s.kv ? 50 + (i % 20) : null,
      bag: 100, bagEx: 91, komEx: 500,
    };
    if (s.mix) {
      r.exU = Math.round(r.ex * 0.6); r.exM = r.ex - r.exU;   // 無人／有人の売上内訳
      // 有人ぶんの来店は手入力。まだ入れていない日もある（入力もれの印が出る側）
      if (i % 9 !== 0) r.vm = 10 + (i % 7);
    }
    daily.push(r);
  }
}
const meta = [];
const metaAds = [];
for (let i = 0; i < DAYS; i++) {
  for (const s of STORES) {
    const stop = (i % 11 === 0);                 // 出稿を止めた日
    const row = {
      d: day(i), a: "ACC", ci: "c" + s.id, c: "CAMP" + s.id,
      st: s.name, si: s.id, ow: s.own,
      sp: stop ? 0 : 1000 + i * 5, im: stop ? 0 : 9000 + i * 20,
      rc: stop ? 0 : 7000, ck: stop ? 0 : 200 + i,
    };
    // 指標は「来る日と来ない日がある」。0埋めしていないことを確かめたいので、
    // わざと歯抜けにする
    if (i % 3 !== 0) { row.pv = 150 + i; row.lk = 30 + i; }
    if (i % 4 !== 0) row.lp = 60 + i;
    if (i % 5 !== 0) { row.vt = 3 + (i % 4); row.vp = 100 + i * 3; }
    if (i % 6 !== 0) row.fl = 5 + (i % 3);
    meta.push(row);
    metaAds.push({
      d: day(i), an: "詰め放題（リール）", c: "CAMP" + s.id,   // 店をまたいで同じ広告名
      ai: "ad" + s.id, ac: "1234567890",
      st: s.name, si: s.id,
      // 1日あたり AD_CR_MINSPEND(1,000円) 未満はランキングから外れる。
      // それだと表が常に空になり、検査にならないので上回る額にしてある
      sp: stop ? 0 : 1800 + i * 10, im: stop ? 0 : 5000, rc: 4000, ck: stop ? 0 : 120,
      ...(i % 3 !== 0 ? { pv: 80 + i } : {}), ...(i % 4 !== 0 ? { lp: 30 + i } : {}),
      ...(i % 5 !== 0 ? { vt: 4, vp: 90 } : {}),
    });
  }
}
// 店舗に紐づいていないキャンペーン（区分が決まらない）
for (let i = 0; i < DAYS; i += 2) {
  meta.push({ d: day(i), a: "ACC", ci: "cX", c: "未紐付けCAMP",
    st: null, si: null, ow: null, sp: 300, im: 2000, rc: 1500, ck: 40 });
}
const DATA = {
  generated_at: "2026-08-30T06:46:00+09:00",
  stores: STORES, daily,
  meta, metaAds, metaDests: [{ id: "d1", name: "NTMいわき", s: 1, ow: "直営" }],
  metaSync: { at: "2026-08-30T06:00:00+09:00", status: "ok", unmapped: 1, rows: 100 },
  cat: [], catp: [], bundles: [], targets: {},
};

// ---- 開く画面 ----------------------------------------------------------
const VIEWS = [
  ["店舗一覧（直営）", "?g=own"],
  ["店舗一覧（FC）", "?g=fc"],
  ["店舗ページ", "?g=own&store=1"],
  ["店舗ページ（区分なしの店）", "?g=own&store=3"],
  ["店舗ページ（FC店）", "?g=fc&store=2"],
  ["店舗ページ（2レジの店）", "?g=own&store=5"],
  ["広告タブ（直営）", "?view=ads&g=own"],
  ["広告タブ（FC）", "?view=ads&g=fc"],
];
// 店舗ページに広告の段が出るか。店名では決めず、その店に広告データがあるかで決まる。
// 直営でもFCでも、2レジの店でも、同じように出ること。
const ADPANEL = new Set(["店舗ページ", "店舗ページ（FC店）", "店舗ページ（2レジの店）",
                         "店舗ページ（区分なしの店）"]);

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "avend-smoke-"));
// __DATA__ を先に入れておくと、boot が通信せずそのまま描く
const page = src.replace("<script>",
  "<script>\nwindow.__ERRORS__=[];\n" +
  "window.addEventListener('error',e=>window.__ERRORS__.push('error: '+(e.message||e)));\n" +
  "window.addEventListener('unhandledrejection',e=>window.__ERRORS__.push('reject: '+(e.reason&&e.reason.message||e.reason)));\n" +
  "(function(){const ce=console.error;console.error=function(){window.__ERRORS__.push('console.error: '+[].slice.call(arguments).join(' '));ce.apply(console,arguments);};})();\n" +
  "window.__DATA__=" + JSON.stringify(DATA) + ";\n" +
  // 描き終わったころに、拾ったエラーを本文へ書き出す（--dump-dom で読むため）
  "window.addEventListener('load',()=>setTimeout(()=>{const d=document.createElement('div');" +
  "d.id='smoke-errs';d.style.display='none';" +
  "d.textContent=(window.__ERRORS__||[]).map(x=>'__SMOKE_ERR__'+x+'__END__').join('');" +
  "document.body.appendChild(d);},1200));\n</script>\n<script>", 1);
const file = path.join(dir, "smoke.html");
fs.writeFileSync(file, page);

let ng = 0;
const open = (query) => {
  const dom = execFileSync(CHROME, [
    ...(IS_SHELL ? [] : ["--headless"]), "--no-sandbox", "--disable-gpu",
    "--window-size=393,900", "--virtual-time-budget=6000",
    "--dump-dom", "file://" + file + query,
  ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], maxBuffer: 64 << 20 });
  return dom;
};

for (const [name, query] of VIEWS) {
  let dom = "";
  try { dom = open(query); }
  catch (e) { console.log(`  NG  ${name}：開けませんでした（${String(e).slice(0, 80)}）`); ng++; continue; }
  // --dump-dom はスクリプトの中身もそのまま返す。仕込んだ文字列や showError の
  // テンプレートに自分で反応してしまうので、まず <script> を落としてから見る
  const view = dom.replace(/<script[\s\S]*?<\/script>/g, "");
  const errs = [...view.matchAll(/__SMOKE_ERR__(.*?)__END__/g)].map((m) => m[1]);
  const app = /<div class="wrap" id="app">([\s\S]*?)<\/div>\s*<nav/.exec(view);
  const body = app ? app[1] : view;
  const ok = (label, cond, extra) => {
    console.log(`  ${cond ? "OK " : "NG "} ${label}`);
    if (!cond) { ng++; if (extra) console.log("      " + String(extra).slice(0, 300)); }
  };
  ok(`${name}：エラーが出ていない`, errs.length === 0, errs.join(" / "));
  ok(`${name}：「読み込めませんでした」が出ていない`, !/class="err"/.test(view),
    (/class="err">([^<]*)/.exec(view) || [])[1]);
  ok(`${name}：中身が描かれている`, body.replace(/<[^>]*>/g, "").trim().length > 80);
  // 表が「該当なし」のまま通ると、検査したつもりで何も見ていないことになる。
  // クリエイティブ別があるのは広告タブだけ（CSSにも #adcrtbl が出てくるので、
  // 文字列の有無ではなく画面で判定する）
  // 広告を全店へ広げた（店名の関門を外した）ので、直営・FC・2レジの店で同じように
  // 出ることを見る。段だけでなく、日別推移の広告費列とKPIまとめの広告行も見る
  //   ＝「段は出たが表の列は出ていない」という中途半端な状態を見逃さないため。
  if (ADPANEL.has(name)) {
    ok(`${name}：広告（Meta）の段が出ている`, /広告（Meta）/.test(body));
    ok(`${name}：日別推移に広告費の列が出ている`, /<th>広告費<\/th>/.test(body));
    const k2 = (/<table class="kmat" id="kpi2">([\s\S]*?)<\/table>/.exec(view) || [])[1] || "";
    ok(`${name}：KPIまとめに広告費とCPCの行が出ている`,
      /広告費/.test(k2) && /CPC/.test(k2), k2 ? "表はあるが広告の行が無い" : "kpi2 の表が無い");
  }
  // 2レジの店（下北沢）だけの作り。広告を足したせいで消えていないこと
  if (name === "店舗ページ（2レジの店）") {
    ok(`${name}：無人／有人の内訳の説明が残っている`, /無人営業（SIPOS）/.test(body));
    ok(`${name}：「有人来店を入力」ボタンが残っている`, /id="stf-btn"/.test(body));
  }
  if (name.startsWith("広告タブ")) {
    const t = /<table class="kmat" id="adcrtbl">([\s\S]*?)<\/table>/.exec(view);
    const rows = t ? (t[1].match(/<tr>/g) || []).length : 0;
    ok(`${name}：クリエイティブ別に行が出ている（${rows - 1}件）`,
      rows > 1 && !/この条件に合うクリエイティブがありません/.test(t ? t[1] : ""));
  }
}

// ---- 期間の粒度（日／週／月／全期間）-----------------------------------
//   既定は「日」。「全期間」はデータのある最初の月から最新の月まで、月のマスで
//   全部並べる（◀▶は送る先が無いので押せなくする）。
//   ボタンを実際に押して確かめる＝押したときに何が起きるかまで見る。
{
  const probe = (grain) => `window.addEventListener('load',function(){setTimeout(function(){
    var out={};
    try{
      out.grain0=state.grain;
      if(${JSON.stringify(grain)}){
        var b=document.querySelector('.gchip[data-grain="'+${JSON.stringify(grain)}+'"]');
        if(!b){out.err='ボタンが無い';}else{b.click();}
      }
      out.grain=state.grain;
      out.keys=trendKeys().length;
      out.first=trendKeys()[0]; out.last=trendKeys()[trendKeys().length-1];
      out.range=(document.getElementById('trend-range')||{}).textContent||'';
      var on=[].slice.call(document.querySelectorAll('.gchip[data-grain].on')).map(function(x){return x.dataset.grain;});
      out.on=on.join(',');
      out.pager=['tr-prev','tr-next'].map(function(i){var e=document.getElementById(i);return e?(e.disabled?'off':'on'):'—';}).join('/');
      out.xlabels=[].slice.call(document.querySelectorAll('#trendchart text')).map(function(t){return t.textContent;}).join(' ');
    }catch(e){ out.err=String(e&&e.message||e); }
    var d=document.createElement('div');d.id='grainprobe';d.style.display='none';
    d.textContent='__G__'+JSON.stringify(out)+'__END__';document.body.appendChild(d);},1400);});`;
  const run = (grain, query) => {
    const f2 = path.join(dir, "grain-" + (grain || "default") + ".html");
    fs.writeFileSync(f2, src.replace("<script>",
      "<script>\nwindow.__DATA__=" + JSON.stringify(DATA) + ";\n" + probe(grain) + "\n</script>\n<script>", 1));
    const dom = execFileSync(CHROME, [...(IS_SHELL ? [] : ["--headless"]), "--no-sandbox",
      "--disable-gpu", "--window-size=393,900", "--virtual-time-budget=7000",
      "--dump-dom", "file://" + f2 + (query || "?g=own&store=5")],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], maxBuffer: 64 << 20 });
    const m = /__G__(.*?)__END__/.exec(dom.replace(/<script[\s\S]*?<\/script>/g, ""));
    return m ? JSON.parse(m[1]) : null;
  };
  const ok = (label, cond, extra) => {
    console.log(`  ${cond ? "OK " : "NG "} ${label}`);
    if (!cond) { ng++; if (extra) console.log("      " + String(extra).slice(0, 300)); }
  };
  // 作り物のデータが何か月ぶんあるか（＝全期間で並ぶはずのマスの数）
  const months = new Set(daily.map((r) => r.d.slice(0, 7)));

  const d0 = run(null);
  if (!d0) { console.log("  NG  粒度の確認：画面から値を取れませんでした"); ng++; }
  else {
    ok(`既定は「日」（いま ${d0.grain0}）`, d0.grain0 === "day", JSON.stringify(d0));
    ok("「日」で押されている印が付くのは1つだけ", d0.on === "day", d0.on);
    ok("「日」では◀▶が押せる", d0.pager === "on/on", d0.pager);
  }
  for (const [g, label] of [["week", "週"], ["month", "月"]]) {
    const d = run(g);
    if (!d) { console.log(`  NG  「${label}」に切り替えられませんでした`); ng++; continue; }
    ok(`「${label}」に切り替わる`, d.grain === g && d.on === g, JSON.stringify(d));
    ok(`「${label}」では◀▶が押せる`, d.pager === "on/on", d.pager);
  }
  const da = run("all");
  if (!da) { console.log("  NG  「全期間」に切り替えられませんでした"); ng++; }
  else {
    ok("「全期間」に切り替わる", da.grain === "all" && da.on === "all", JSON.stringify(da));
    ok(`「全期間」はデータのある月を全部並べる（${da.keys}マス／データは${months.size}か月）`,
      da.keys === months.size, `${da.first}〜${da.last} / ${[...months].sort().join(",")}`);
    ok("「全期間」では◀▶を押せなくする", da.pager === "off/off", da.pager);
    ok("「全期間」の見出しは最初の月から最新の月まで", /年.*月.*〜.*年.*月/.test(da.range), da.range);
    // 年をまたぐので、1月と左端には年を付ける（「10月」が2回出ると見分けが付かない）
    ok("年をまたぐ目盛りに年が付いている", /\d\d年\d+月/.test(da.xlabels || ""), da.xlabels);
    ok("「全期間」でも例外が出ていない", !da.err, da.err);
  }
  // 店舗一覧の④売上推移は別の関数（drawTrend2）が描いていて、こちらは表も作る。
  // 同じ「全期間」で落ちないことを見る（片方だけ直して片方が壊れる、を防ぐ）
  // あとから開いた店は、その店に売上がある月だけを並べる（全店の最古から始めない）
  const dn = run("all", `?g=own&store=${LATE}`);
  if (!dn) { console.log("  NG  あとから開いた店で「全期間」に切り替えられませんでした"); ng++; }
  else {
    const lateMonths = new Set(daily.filter((r) => r.s === LATE).map((r) => r.d.slice(0, 7)));
    ok(`あとから開いた店の「全期間」はその店の範囲だけ（${dn.keys}マス／その店は${lateMonths.size}か月）`,
      dn.keys === lateMonths.size, `${dn.first}〜${dn.last}`);
    ok("あとから開いた店の「全期間」が全店の最古から始まっていない",
      dn.keys < months.size, `${dn.keys} vs 全店 ${months.size}`);
  }
  const dl = run("all", "?g=own");
  if (!dl) { console.log("  NG  店舗一覧の推移で「全期間」に切り替えられませんでした"); ng++; }
  else {
    ok("店舗一覧の推移も「全期間」に切り替わる", dl.grain === "all" && !dl.err,
      JSON.stringify(dl));
    ok(`店舗一覧の推移も月を全部並べる（${dl.keys}マス）`, dl.keys === months.size,
      `${dl.first}〜${dl.last}`);
  }
}

// ---- サブページ（推移・予実・販促・分析AI・経営）------------------------
//   日別×店舗は 2026-08-11 に data.json からテーブル(dash_daily)へ移した。
//   dashboard.html だけ直され、他のページは DATA.daily が空のまま
//   「データが空です」で止まっていた（1か月以上そのままだった）。
//   同じ取り残しが起きないよう、DATA.daily を使うページは必ず
//   dash_daily を読んでいること、を全ページで見る。
{
  const dir2 = path.join(__dirname, "..", "web");
  const pages = fs.readdirSync(dir2).filter((f) => f.endsWith(".html"));
  const ok = (label, cond, extra) => {
    console.log(`  ${cond ? "OK " : "NG "} ${label}`);
    if (!cond) { ng++; if (extra) console.log("      " + String(extra).slice(0, 300)); }
  };
  const missing = [], noStock = [];
  for (const f of pages) {
    const t = fs.readFileSync(path.join(dir2, f), "utf8");
    if (/(?<![.\w$])DATA\.daily/.test(t) && !/dash_daily/.test(t)) missing.push(f);
    if (/stock\.html/.test(t)) noStock.push(f);
  }
  ok(`日別を使うページは全部 dash_daily を読んでいる（${pages.length}ページ）`,
    missing.length === 0, missing.join(", "));
  // 棚卸は在庫アプリの /pos-stocktake に移した。売上アプリ側には残さない
  ok("棚卸(stock.html)への導線が残っていない", noStock.length === 0, noStock.join(", "));
  ok("棚卸のページ自体が残っていない", !pages.includes("stock.html"));
}

// ---- 触っているのに存在しない id が無いか ------------------------------
//   パネルを消したときに、それを描く関数だけ残ることがある。呼ばれていないうちは
//   無害だが、あとで誰かが呼ぶと null に .innerHTML して落ちる。
//   いま呼ばれていないもの（＝残骸）は、名前を挙げて分かるようにしておく。
{
  const ids = new Set([...src.matchAll(/id="([a-zA-Z][\w-]*)"/g)].map((m) => m[1]));
  const used = [...src.matchAll(/getElementById\("([^"$]+)"\)/g)].map((m) => m[1]);
  const miss = [...new Set(used.filter((u) => !ids.has(u)))];
  // その id を触っている関数が、実際に呼ばれているかどうかで重さが変わる
  const live = [];
  for (const id of miss) {
    const at = src.indexOf(`getElementById("${id}")`);
    const head = src.lastIndexOf("\nfunction ", at);
    const fn = head < 0 ? null : /\nfunction\s+([A-Za-z_$][\w$]*)/.exec(src.slice(head))[1];
    if (!fn) { live.push(id + "（関数が特定できません）"); continue; }
    const calls = (src.match(new RegExp("(?<![.\\w$])" + fn + "\\s*\\(", "g")) || []).length - 1;
    if (calls > 0) live.push(`${id}（${fn} が ${calls}回 呼ばれています）`);
  }
  if (live.length) {
    console.log("  NG  存在しない id を、呼ばれている関数が触っています");
    live.forEach((l) => console.log("      " + l));
    ng++;
  } else if (miss.length) {
    console.log(`  OK  存在しない id を触るのは、呼ばれていない関数だけ（残骸 ${miss.length}件: ${miss.join(", ")}）`);
  } else console.log("  OK  存在しない id を触っている箇所は無い");
}

console.log(ng ? `\n❌ ${ng}件ずれています。` : "\n✅ どの画面もエラーなく描けています。");
process.exit(ng ? 1 : 0);
