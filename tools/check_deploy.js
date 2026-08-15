/*
 * 「直したのに本番に出ていない」を見つける。
 *
 *     node tools/check_deploy.js
 *
 * 【なぜ要るのか】
 *   このアプリは web/*.html を gh-pages ブランチへコピーしたものが本番になる。
 *   コピーしているのは deploy-pages.yml で、以前は配信するファイルを1枚ずつ
 *   名指ししていた。名指しから漏れたページは、直しても本番に届かない。
 *
 *   実際に 推移・予実・販促・分析AI・経営 の5枚が 2026-08-11 から1か月以上
 *   止まっていた。画面には「データが空です」と出ていたが、リポジトリ側の
 *   コードは正しかったので、コードを読んでも原因が分からない状態だった。
 *   さらに pl.html / keiei.html は、配信物のほうにしか無い実装（着地見込みの
 *   祝日判定）を持っていて、うっかり web/ で上書きすると数字が静かに変わる
 *   ところだった。
 *
 *   どちらも「web/ と gh-pages がずれている」という一つの症状なので、
 *   ここで突き合わせて、ずれていたら知らせる。
 *
 * 【注意】
 *   gh-pages を取っていない環境では測れないので、その場合は飛ばす（落とさない）。
 *   ずれの向きまでは判定しない。どちらが新しいかは人が見て決める
 *   （配信物のほうが新しいことが実際にあった）。
 */
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = path.join(__dirname, "..");
const WEB = path.join(ROOT, "web");

// dashboard.html だけ、配信先では index.html という名前になる
const DEPLOY_NAME = (f) => (f === "dashboard.html" ? "index.html" : f);

const git = (args) =>
  execFileSync("git", args, { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });

let ref = null;
for (const r of ["origin/gh-pages", "gh-pages"]) {
  try { git(["rev-parse", "--verify", r]); ref = r; break; } catch { /* 次を試す */ }
}
if (!ref) {
  console.log("  --  gh-pages を取っていないので、配信の突き合わせは飛ばします");
  console.log("      （見たいときは git fetch origin gh-pages）");
  process.exit(0);
}

const pages = fs.readdirSync(WEB).filter((f) => f.endsWith(".html")).sort();
const deployed = new Set(
  git(["ls-tree", "--name-only", ref]).split("\n").filter((n) => n.endsWith(".html")));

let ng = 0;
const diff = [], missing = [], extra = [];

for (const f of pages) {
  const name = DEPLOY_NAME(f);
  if (!deployed.has(name)) { missing.push(`${f} → ${name}`); continue; }
  const here = fs.readFileSync(path.join(WEB, f), "utf8");
  const there = git(["show", `${ref}:${name}`]);
  if (here !== there) diff.push(`${f} ↔ ${name}`);
}
// 配信先にあって web/ に無いもの（消したはずのページが残っている）
for (const name of deployed) {
  const src = name === "index.html" ? "dashboard.html" : name;
  if (!pages.includes(src)) extra.push(name);
}

const ok = (label, cond, lines) => {
  console.log(`  ${cond ? "OK " : "NG "} ${label}`);
  if (!cond) { ng++; (lines || []).forEach((l) => console.log("      " + l)); }
};

console.log(`本番（${ref}）と web/ の突き合わせ`);
ok(`web/ のページが全部配信されている（${pages.length}枚）`, missing.length === 0, missing);
ok("配信されている中身が web/ と同じ", diff.length === 0,
  diff.concat(diff.length ? ["→ どちらが新しいかは中身を見て決めること。",
    "   配信物のほうが新しいことが実際にあった（pl.html / keiei.html の祝日判定）。",
    "   確認: git diff --no-index web/pl.html <(git show " + ref + ":pl.html)"] : []));
ok("web/ から消したページが配信先に残っていない", extra.length === 0, extra);

console.log(ng ? `\n❌ ${ng}件ずれています。直しても本番に出ていない可能性があります。`
               : "\n✅ 本番と web/ は一致しています。");
process.exit(ng ? 1 : 0);
