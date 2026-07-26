"""
ブラウザ操作（Playwright）の共通部分

cashier もデジテールも「ブラウザでログインしないとCSVが取れない」ので、
ヘッドレス（画面を出さない）Chromium を使う。

【失敗したときに必ずやること】
  ・その瞬間の画面のスクリーンショットとHTMLを debug/ に保存する
  ・さらに「入力欄・ボタンの一覧」と「通信の記録」も debug/ に .txt で残す
  → 画面が変わって動かなくなったとき、原因がすぐ分かる。
    とくに cashier の「期間欄」「CSVボタン」の目印（セレクタ）は、
    この .txt を見れば、実際にログインした後の本物の画面から確定できる。
    GitHub Actions では、この debug/ を成果物としてダウンロードできるようにしている。
    → スマホからでも「Actionsを1回動かす → Artifactのtxtを見る」だけで
       目印を確定でき、手元PCでのログイン調査（inspect_cashier.py）が不要になる。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from .settings import BROWSER_TIMEOUT_MS, DEBUG_DIR, EtlError

# 通信ログを1ページあたり最大何件まで覚えておくか（メモリ暴走の予防）
_MAX_REQUESTS = 400
# レポートに載せる通信ログの件数（末尾からこの件数）
_REPORT_REQUESTS = 60

# ------------------------------------------------------------
# 画面の中の「入力欄」と「ボタン・リンク」を洗い出すJavaScript。
# 目に見えている（幅も高さもある）ものだけを対象にする。
# ここが cashier の期間欄・CSVボタンの目印を確定する材料になる。
# ------------------------------------------------------------
SCAN_JS = """
() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const desc = el => ({
    tag: el.tagName,
    type: el.type || null,
    name: el.name || null,
    id: el.id || null,
    placeholder: el.placeholder || null,
    value: (el.value || '').slice(0, 40),
    ariaLabel: el.getAttribute('aria-label'),
    dataTestId: el.getAttribute('data-testid') || el.getAttribute('data-test'),
    href: el.getAttribute ? (el.getAttribute('href') || null) : null,
    onclick: (el.getAttribute && el.getAttribute('onclick')) ? true : null,
    disabled: (el.disabled || el.getAttribute('aria-disabled') === 'true') || null,
    className: (el.className || '').toString().slice(0, 120),
    text: (el.innerText || '').trim().slice(0, 60),
  });
  const dateSel = '[class*="date" i],[class*="calendar" i],[class*="picker" i],'
                + '[id*="date" i],[placeholder*="日"],[aria-label*="日"],'
                + '[placeholder*="期間"],[aria-label*="期間"],[role="combobox"]';
  return {
    url: location.href,
    title: document.title,
    inputs:  [...document.querySelectorAll('input, select, textarea')].filter(vis).map(desc),
    dateish: [...document.querySelectorAll(dateSel)].filter(vis).slice(0, 40).map(desc),
    buttons: [...document.querySelectorAll('button, a, [role=button]')].filter(vis)
              .filter(e => (e.innerText || '').trim().length > 0).map(desc),
  };
}
"""

# 取引明細リンク（例 "0002-0002-20260726-0044"）はレポートでは邪魔なので除外する
import re as _re
_TX_RE = _re.compile(r"^\d{3,4}-\d{3,4}-\d{6,8}-\d{3,4}$")
_MAX_BUTTONS = 40


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        raise EtlError(
            "Playwright（ブラウザ操作の部品）が入っていません。\n"
            "  → 次の2つを順番に実行してください:\n"
            "       pip install -r requirements.txt\n"
            "       python -m playwright install chromium"
        )


@contextmanager
def browser_page(headless: bool = True, accept_downloads: bool = True):
    """
    ブラウザを開いて1ページ渡す。使い終わったら自動で閉じる。

    使い方:
        with browser_page() as (page, context):
            page.goto("https://...")

    ページには通信の記録を自動でためておく（page 上の属性）。
    CSVボタンの正体が「あるURLへのGET」だった場合、
    失敗時の調査レポートにそのURLが出るので、目印の確定に役立つ。
    """
    sync_playwright = _import_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context(
            accept_downloads=accept_downloads,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1440, "height": 900},
        )
        context.set_default_timeout(BROWSER_TIMEOUT_MS)
        page = context.new_page()
        main_log = attach_request_log(page)

        # 確認ダイアログ（「ダウンロードしますか？」等）はデフォルトだと
        # Playwright が自動で打ち消してエクスポートがキャンセルされる。
        # 出たら必ず「OK」して先へ進める。
        def _accept_dialog(d) -> None:
            try:
                main_log.append(f"[dialog] {d.type}: {(d.message or '')[:80]}")
                d.accept()
            except Exception:
                try:
                    d.dismiss()
                except Exception:
                    pass
        try:
            page.on("dialog", _accept_dialog)
        except Exception:
            pass

        # 別タブ（ポップアップ）で開くCSV出力などの通信も、同じログにためる。
        def _hook_popup(popup) -> None:
            try:
                popup.on("dialog", _accept_dialog)
                popup.on("request",
                         lambda r: main_log.append(f"[popup] {r.method} {r.url}"))
                popup.on("download",
                         lambda d: main_log.append(f"[popup] DOWNLOAD {d.url}"))
            except Exception:
                pass
        try:
            context.on("page", _hook_popup)
        except Exception:
            pass
        try:
            yield page, context
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()


def attach_request_log(page) -> list[str]:
    """
    このページの通信（リクエスト／ダウンロード）を記録し始める。
    記録は page._notime_requests に list で入り、dump_page が拾って残す。
    """
    log: list[str] = []

    def _remember(entry: str) -> None:
        log.append(entry)
        if len(log) > _MAX_REQUESTS:
            del log[: len(log) - _MAX_REQUESTS]

    try:
        page.on("request", lambda r: _remember(f"{r.method} {r.url}"))
        page.on("download", lambda d: _remember(f"DOWNLOAD {d.url}"))
    except Exception:
        pass
    page._notime_requests = log
    return log


def scan_page(page) -> dict:
    """画面の入力欄・ボタン・リンクを洗い出して返す（目印の確定材料）。"""
    return page.evaluate(SCAN_JS)


def _mask_url(entry: str) -> str:
    """
    'GET https://host/path?a=xxx&b=yyy' の クエリ値だけを *** に伏せる。
    APIの入口とパラメータ名（date, from, store 等）は残し、トークン等の値は隠す。
    """
    if "?" not in entry:
        return entry
    head, query = entry.split("?", 1)
    parts = []
    for kv in query.split("&"):
        if "=" in kv:
            k, _ = kv.split("=", 1)
            parts.append(f"{k}=***")
        else:
            parts.append(kv)
    return head + "?" + "&".join(parts)


def format_scan_report(info: dict, requests_log: list[str] | None = None,
                       mask_requests: bool = False) -> str:
    """
    scan_page の結果を、人が読める（＝そのまま貼れる）テキストに整える。
    inspect_cashier.py と、失敗時の dump_page で共通に使う。

    ・取引明細リンクの洪水は除外し、ボタンは上限 _MAX_BUTTONS 件まで
    ・mask_requests=True のとき、通信URLのクエリ値を伏せ字にする（ログ出力用）
    """
    buttons = [b for b in info.get("buttons", [])
               if not _TX_RE.match((b.get("text") or "").strip())]
    hidden = len(info.get("buttons", [])) - len(buttons)
    shown = buttons[:_MAX_BUTTONS]
    button_note = ""
    if len(buttons) > _MAX_BUTTONS:
        button_note = f"（ボタンは先頭{_MAX_BUTTONS}件のみ表示 / 全{len(buttons)}件）"
    if hidden:
        button_note += f"（取引明細リンク {hidden}件は省略）"

    lines = [
        "===== 画面調査（入力欄・ボタン・通信）=====",
        f"URL  : {info.get('url')}",
        f"件名 : {info.get('title')}",
        "",
        "----- 入力欄（input/select/textarea）-----",
        json.dumps(info.get("inputs", []), ensure_ascii=False, indent=2),
        "",
        "----- 日付・カレンダーらしき要素（期間指定の候補）-----",
        json.dumps(info.get("dateish", []), ensure_ascii=False, indent=2),
        "",
        f"----- ボタン・リンク（CSVダウンロード等）{button_note} -----",
        json.dumps(shown, ensure_ascii=False, indent=2),
    ]
    if requests_log:
        filtered = [
            line for line in requests_log
            if any(k in line.lower()
                   for k in ("csv", "download", "export", "trade", "search",
                             "api", "report", "sales"))
        ]
        if mask_requests:
            filtered = [_mask_url(x) for x in filtered]
        # 同じURLの重複を畳む（値を伏せると重複が増えるため）
        deduped = list(dict.fromkeys(filtered))
        lines += [
            "",
            "----- 通信の記録（データ/CSV取得のAPIが分かることがあります）-----",
            *(deduped[-_REPORT_REQUESTS:] or ["（該当する通信は記録されていません）"]),
        ]
    return "\n".join(lines)


def dump_page(page, label: str) -> Path | None:
    """
    今の画面をスクリーンショット＋HTML＋「入力欄・ボタンの一覧(.txt)」で保存する。
    エラー調査用。失敗しても本体は止めない。

    .txt には、その瞬間の画面の入力欄・ボタン・通信が入る。
    cashier の期間欄／CSVボタンの目印は、これを見れば確定できる。
    """
    png: Path | None = None
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        png = DEBUG_DIR / f"{label}.png"
        html = DEBUG_DIR / f"{label}.html"
        page.screenshot(path=str(png), full_page=True)
        html.write_text(page.content(), encoding="utf-8")
        print(f"  🔍 調査用に画面を保存しました: {png} / {html}")
    except Exception as e:
        print(f"  （画面の保存はできませんでした: {e}）")
        png = None

    # 入力欄・ボタン・通信の一覧（目印の確定に一番使う）
    try:
        info = scan_page(page)
        # ファイルには通信の記録（CSVのURL等）も含める。
        report = format_scan_report(info, getattr(page, "_notime_requests", None))
        report_path = DEBUG_DIR / f"{label}_report.txt"
        report_path.write_text(report, encoding="utf-8")
        print(f"  🔍 入力欄・ボタンの一覧を保存しました: {report_path}")

        # 実行ログ（GitHub Actions のログ）にも入力欄・ボタン・通信を出す。
        # → Artifactを落とさなくても、ログを見るだけで目印やAPIを確定できる。
        #   通信URLはクエリ値を伏せ字(***)にして、入口とパラメータ名だけを残す。
        stdout_report = format_scan_report(
            info, getattr(page, "_notime_requests", None), mask_requests=True
        )
        print("  ----8<---- ここから画面調査（セレクタ確定用） ----8<----")
        for line in stdout_report.splitlines():
            print(f"  | {line}")
        print("  ----8<---- ここまで ----8<----")
    except Exception as e:
        print(f"  （入力欄・ボタンの一覧は保存できませんでした: {e}）")

    return png
