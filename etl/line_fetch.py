"""
LINE配信データの取り込み（デジテールストア）

【使い方】
  python -m etl.line_fetch --all                    過去分をすべて取り込む（初回）
  python -m etl.line_fetch --from 2024-01-01        指定日から今日まで
  python -m etl.line_fetch                          前日ぶんだけ（毎日の自動実行）
  python -m etl.line_fetch --probe                  置き場所を調べ直す（画面が変わったとき）

【この作りにした理由】
  来店数と同じ仕組みをそのまま使う。デジテールストア（開発元＝ネブラスカ）は
  来店・売上・LINEを同じ管理画面で持っているので、
    ブラウザでログイン → その状態のまま取りに行く → 整えて Supabase へ
  という流れを etl/digitel_fetch.py と共有できる。
  **認証情報も同じ**（DIGITAIL_ID / DIGITAIL_PW）。新しく登録するものは無い。

【どこから何を取るか】※2026-08-28 に実機で確認
  友だち数 … /{slug}/kpi/members/friends/download?interval=day&from=&to=
             CSVで返る。列は「日付, 新規友だち登録数, 累積友だち登録数」
  配信履歴 … /{slug}/messages/history
             CSVの出口が無い（/download は404）ので、画面の表を読む。
             列は「配信日時 / 配信人数 / メッセージタイプ / 開封率 / メッセージ」。
             10件ずつのページ送りなので、次ページを押しながら集める。

【取れないもの（画面に無い）】
  ・クリック数／クリック人数  … 配信履歴に列が無い
  ・ブロック数               … 友だちCSVに列が無い
  ・友だち追加の経路別        … 画面が見当たらない
  ・クーポン使用数           … クーポン一覧はあるが実績の数字が無い
  いずれも列は用意してあるので、画面に出るようになれば入れられる。

【店舗】
  来店数と同じ digitel_fetch.discover_stores() で「店舗名 → スラッグ」を拾う。
  NOTIME（DIGITAIL_ID/PW）と SELFURUGI（DIGITAIL_SF_ID/PW）の2アカウントを回る。
  line_accounts.account_id はデジテールのスラッグ（例 notime_fukui）。
  店舗との紐付け（line_accounts.store_id ＝ POS店舗）は後から人が埋める。

【設定（任意・GitHub の Variables）】
  DIGITEL_LINE_FRIENDS_PATH   友だちCSVのパス。画面が変わったときだけ使う
  DIGITEL_LINE_COLMAP         CSVの見出しが変わったときの対応表(JSON)
  DIGITEL_LINE_HISTORY_FROM   --all の開始日。既定 "2019-01-01"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

from . import digitel_fetch
from .browser import browser_page, dump_page
from .settings import (
    DIGITEL_BASE_URL,
    JST,
    EtlError,
    load_dotenv,
    new_run_id,
    validate_date,
    yesterday_jst,
)
from .supabase_client import Supabase


# ============================================================
#  設定の読み出し
# ============================================================
def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _require(name: str) -> str:
    v = _env(name)
    if not v:
        raise EtlError(
            f"{name} が未設定です。\n"
            f"  → GitHub の Settings → Secrets and variables → Actions に登録してください。\n"
            f"  → 何を入れるかは etl/line_fetch.py の先頭に書いてあります。"
        )
    return v


# CSVの見出し → こちらの列名。実物に合わせて DIGITEL_LINE_COLMAP で上書きできる。
# 1つの列に複数の呼び名を並べてあるのは、画面の言い回しが分からないうちに
# 決め打ちすると外れるため。
DEFAULT_ALIASES: dict[str, list[str]] = {
    "sent_at":      ["配信日時", "送信日時", "配信日", "日時", "sent_at"],
    "title":        ["配信名", "メッセージ名", "タイトル", "件名", "title"],
    "kind":         ["配信種別", "種別", "配信タイプ", "type"],
    "delivered":    ["配信通数", "送信数", "配信数", "delivered", "sent"],
    "opened":       ["開封数", "インプレッション", "表示回数", "opened", "impressions"],
    "clicked":      ["クリック数", "クリック", "clicked", "clicks"],
    "blocked":      ["ブロック数", "ブロック", "blocked"],
    "coupon_used":  ["クーポン使用数", "クーポン利用数", "coupon_used"],
    # 友だち数（日次）
    "date":         ["日付", "date", "集計日"],
    "friends":      ["友だち数", "有効友だち数", "friends"],
    "followers":    ["累積友だち登録数", "累計友だち数", "追加友だち数(累計)", "followers"],
    # ↓ 実物のCSVで確認済み（/{slug}/kpi/members/friends/download）
    "added":        ["新規友だち登録数", "新規友だち数", "友だち追加数", "追加", "added"],
    "net":          ["純増", "増減", "net"],
    "targeted":     ["ターゲットリーチ", "配信可能数", "targeted"],
    # 流入経路
    "source":       ["経路", "流入経路", "追加経路", "source"],
}

# CSVで取れるのは友だち数だけ（配信履歴は画面の表を読む）。
# 画面が変わったら Variables（DIGITEL_LINE_FRIENDS_PATH）で差し替えられる。
DEFAULT_PATHS = {
    "FRIENDS": "/{slug}/kpi/members/friends/download?interval=day&from={from}&to={to}",
}


def _aliases() -> dict[str, list[str]]:
    """既定の対応表に、DIGITEL_LINE_COLMAP で指定された呼び名を足す。"""
    out = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    raw = _env("DIGITEL_LINE_COLMAP")
    if not raw:
        return out
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError as e:
        raise EtlError(f"DIGITEL_LINE_COLMAP がJSONとして読めません: {e}") from e
    for key, names in extra.items():
        vals = names if isinstance(names, list) else [names]
        out.setdefault(key, [])
        out[key] = [str(v) for v in vals] + [v for v in out[key] if v not in vals]
    return out


def _url_for(kind: str, slug: str, start: str, end: str) -> str:
    """CSVのURLを組み立てる。パスは Variables で差し替えられる。"""
    tmpl = _env(f"DIGITEL_LINE_{kind}_PATH") or DEFAULT_PATHS[kind]
    path = (tmpl.replace("{slug}", slug)
                .replace("{from}", start).replace("{to}", end)
                .replace("{start}", start).replace("{end}", end))
    if not path.startswith("http"):
        path = DIGITEL_BASE_URL + ("" if path.startswith("/") else "/") + path
    # 期間の指定がパスに無ければクエリで付ける（来店CSVと同じ形）
    if "{" not in tmpl and "start" not in path and "from" not in path:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}start={start}&end={end}"
    return path


# ============================================================
#  調査モード
# ============================================================
def probe(headless: bool = True) -> int:
    """
    デジテールにログインして、LINE配信データの置き場所を探す。
    来店・売上と同じログイン（DIGITAIL_ID / DIGITAIL_PW）を使う。
    """
    with browser_page(headless=headless) as (page, context):
        digitel_fetch.login(page)
        print(f"  ログイン後のURL: {page.url}")

        stores = digitel_fetch.discover_stores(page)
        print(f"  見つかった店舗: {len(stores)}件")
        for nm, sl in list(sorted(stores.items()))[:10]:
            print(f"     {nm} → {sl}")
        if not stores:
            dump_page(page, "digitel_line_probe_nostores")
            raise EtlError("店舗を1件も見つけられませんでした。debug/ を確認してください。")

        slug = sorted(stores.values())[0]
        print(f"\n  この店舗で調べます: {slug}")

        # 画面内のリンクからLINEらしきものを拾う
        for path in ("", "/line", "/kpi", "/kpi/line", "/message", "/broadcast"):
            url = f"{DIGITEL_BASE_URL}/{slug}{path}"
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                code = resp.status if resp else "?"
            except Exception as e:
                print(f"    {url} → 開けません（{str(e)[:60]}）")
                continue
            page.wait_for_timeout(1200)
            try:
                hrefs = page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.getAttribute('href')).filter(Boolean)")
            except Exception:
                hrefs = []
            line_links = [h for h in hrefs if any(
                k in h.lower() for k in ("line", "message", "broadcast", "friend"))]
            mark = "★" if line_links else " "
            print(f"  {mark} {url} → HTTP {code} / リンク{len(hrefs)}件")
            for h in line_links[:10]:
                print(f"       → {h}")

        dump_page(page, "digitel_line_probe")
        print("\n  ★ が付いたURLと、その下のリンクを見て")
        print("     DIGITEL_LINE_BROADCAST_PATH などを Variables に設定してください。")
    return 0


def _fetch_csv(context, url: str) -> str:
    """ログイン済みの状態でCSVのURLを取りに行く（来店数の取り込みと同じやり方）。"""
    resp = context.request.get(url, timeout=120_000)
    if resp.status != 200:
        raise EtlError(f"CSVの取得に失敗しました（HTTP {resp.status}） URL: {url}")
    body = resp.body()
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


# ============================================================
#  配信履歴（画面の表を読む）
# ============================================================
#   /{slug}/messages/history には CSVダウンロードが無い（/download は404）。
#   Remixのローダー（.data）は返るが独自形式で壊れやすいので、画面の表を読む。
#   列: 配信日時 / 配信人数 / メッセージタイプ / 開封率 / メッセージ
def fetch_broadcasts(page, slug: str) -> list[dict]:
    url = f"{DIGITEL_BASE_URL}/{slug}/messages/history"
    page.goto(url, wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(1500)

    rows: list[dict] = []
    seen_first = ""
    for _ in range(200):                     # 200ページ＝2000件で打ち切り（無限ループ避け）
        try:
            got = page.evaluate(r"""() => {
              const tb = document.querySelector("table tbody");
              if(!tb) return [];
              return Array.from(tb.querySelectorAll("tr")).map(tr =>
                Array.from(tr.querySelectorAll("td"))
                     .map(td => (td.innerText||'').trim().replace(/\s+/g,' ')));
            }""")
        except Exception:
            got = []
        if not got:
            break
        first = "|".join(got[0]) if got and got[0] else ""
        if first and first == seen_first:    # ページが進んでいない
            break
        seen_first = first
        rows.extend(got)

        # 次のページへ。押せなくなったら終わり
        try:
            nxt = page.locator("button[aria-label='Go to next page'], "
                               "button:has-text('Go to next page')").first
            if nxt.is_disabled():
                break
            nxt.click(timeout=4_000)
            page.wait_for_timeout(1200)
        except Exception:
            break
    return rows


# ============================================================
#  取り込み本体
# ============================================================
def _run_account(sb: Supabase, user: str | None, pw: str | None, label: str,
                 start: str, end: str, headless: bool) -> dict:
    from .line_rows import broadcast_rows_from_table, daily_rows

    aliases = _aliases()
    total = {"accounts": 0, "daily": 0, "broadcasts": 0}

    with browser_page(headless=headless) as (page, context):
        digitel_fetch.login(page, user, pw)
        stores = digitel_fetch.discover_stores(page)
        print(f"  [{label}] 店舗 {len(stores)}件")
        if not stores:
            return total

        # アカウント台帳。account_id はデジテールのスラッグをそのまま使う
        acct_rows = [{"account_id": slug, "name": name} for name, slug in sorted(stores.items())]
        sb.upsert("line_accounts", acct_rows, on_conflict="account_id")
        total["accounts"] = len(acct_rows)

        for name, slug in sorted(stores.items()):
            # --- 友だち数（CSV。確認済み）---
            try:
                url = _url_for("FRIENDS", slug, start, end)
                text = _fetch_csv(context, url)
                d = daily_rows(text, slug, aliases)
                if d:
                    sb.upsert("line_daily", d, on_conflict="date,account_id")
                total["daily"] += len(d)
                print(f"    {name}: 友だち {len(d)}日ぶん")
            except Exception as e:                       # noqa: BLE001
                print(f"    ⚠️ {name}: 友だちを取れません: {str(e)[:120]}")

            # --- 配信履歴（画面の表）---
            try:
                table = fetch_broadcasts(page, slug)
                b = broadcast_rows_from_table(table, slug, start, end)
                if b:
                    sb.upsert("line_broadcasts", b, on_conflict="broadcast_id")
                total["broadcasts"] += len(b)
                print(f"    {name}: 配信 {len(b)}件")
            except Exception as e:                       # noqa: BLE001
                print(f"    ⚠️ {name}: 配信履歴を取れません: {str(e)[:120]}")

    return total


def run(start: str, end: str, headless: bool = True) -> int:
    load_dotenv()
    sb = Supabase()
    run_id = new_run_id()
    grand = {"accounts": 0, "daily": 0, "broadcasts": 0}

    # NOTIME と SELFURUGI の2アカウント。来店数の取り込みと同じ持ち方。
    accounts = [(None, None, "NOTIME")]
    sf_id, sf_pw = _env("DIGITAIL_SF_ID"), _env("DIGITAIL_SF_PW")
    if sf_id and sf_pw:
        accounts.append((sf_id, sf_pw, "SELFURUGI"))
    else:
        print("  ℹ️ DIGITAIL_SF_ID / PW が未設定のため SELFURUGI はスキップ")

    for user, pw, label in accounts:
        try:
            t = _run_account(sb, user, pw, label, start, end, headless)
            for k in grand:
                grand[k] += t.get(k, 0)
        except Exception as e:                           # noqa: BLE001
            print(f"  ⚠️ [{label}] 取り込みに失敗: {str(e)[:200]}")

    print(f"\n✅ 取り込み完了 {start}〜{end}"
          f"（アカウント {grand['accounts']} / 友だち {grand['daily']}行 / 配信 {grand['broadcasts']}件）"
          f"  run_id={run_id}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="LINE配信データ（デジテールストア）を取り込む")
    ap.add_argument("--from", dest="start", default="", help="開始日 YYYY-MM-DD")
    ap.add_argument("--to", dest="end", default="", help="終了日 YYYY-MM-DD（空なら今日）")
    ap.add_argument("--all", action="store_true", help="取れる限り過去まで遡る")
    ap.add_argument("--probe", action="store_true", help="どこにデータがあるか調べる")
    ap.add_argument("--headed", action="store_true", help="ブラウザの画面を出す（調査用）")
    args = ap.parse_args()

    load_dotenv()
    if args.probe:
        return probe(headless=not args.headed)

    end = validate_date(args.end) if args.end else datetime.now(JST).date().isoformat()
    if args.all:
        start = _env("DIGITEL_LINE_HISTORY_FROM", "2019-01-01")
    elif args.start:
        start = validate_date(args.start)
    else:
        start = yesterday_jst()
    if start > end:
        raise EtlError(f"開始日({start})が終了日({end})より後になっています。")
    print(f"LINE配信データを取り込みます: {start} 〜 {end}")
    return run(start, end, headless=not args.headed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EtlError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
