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
const day = (n) => {
  const t = new Date(Date.UTC(2026, 6, 1) + n * 86400000);
  return t.toISOString().slice(0, 10);
};
const DAYS = 60;
const STORES = [
  { id: 1, name: "NTMいわき", own: "直営", kv: true, visible: true },
  { id: 2, name: "NTM所沢", own: "FC", kv: true, visible: true },
  { id: 3, name: "NTM区分なし", own: null, kv: true, visible: true },   // ownership 未設定
  { id: 4, name: "NTM来店なし", own: "直営", kv: false, visible: true }, // 来店をKPIに使わない
];
const daily = [];
for (let i = 0; i < DAYS; i++) {
  for (const s of STORES) {
    daily.push({
      d: day(i), s: s.id,
      ex: (s.id === 4 && i % 7 === 0) ? null : 80000 + ((i * 37 + s.id * 11) % 40000),
      in: 90000, tx: 20 + (i % 9), it: 40 + (i % 5),
      v: s.kv ? 50 + (i % 20) : null,
      bag: 100, bagEx: 91, komEx: 500,
    });
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
  ["広告タブ（直営）", "?view=ads&g=own"],
  ["広告タブ（FC）", "?view=ads&g=fc"],
];

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
  if (name.startsWith("広告タブ")) {
    const t = /<table class="kmat" id="adcrtbl">([\s\S]*?)<\/table>/.exec(view);
    const rows = t ? (t[1].match(/<tr>/g) || []).length : 0;
    ok(`${name}：クリエイティブ別に行が出ている（${rows - 1}件）`,
      rows > 1 && !/この条件に合うクリエイティブがありません/.test(t ? t[1] : ""));
  }
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
