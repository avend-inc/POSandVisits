"""
cashier（キャッシャー）から売上明細CSVを取ってくる

【実際のログイン画面で確認した仕様（2026-07-23 に確認）】
  ・https://cashier.jp/v2/client/trade を開くと
    https://login.cashier.jp/login/?ref=... へ自動で飛ばされる
  ・入力欄は「メールアドレス」と「パスワード」の2つ、送信は「ログイン」ボタン
  ・⚠️ 入力欄の name 属性は両方とも "hogehogehogehoge" というダミーで、
    id は画面を開くたびに変わる（_r_1_ など）。
    → name や id を目印にしてはいけない。
       type="text" / type="password" / type="submit" で指定する。
  ・JavaScriptで動くログイン画面なので、ブラウザ（Playwright）が必要。

【取引履歴一覧の操作手順（2026-07-26 に実画面で確定・動作確認済み）】
  1. 期間欄は「検索オプション」(a.collapsed / href=#search-details) の中に
     隠れている → まず開く（open_search_options）。
  2. 開始日・終了日を対象日にして「検索」ボタンで絞り込む。
  3. 「CSV出力(明細)」ボタン（button.btn-info / data-toggle=modal /
     data-target=#download-format-selector-modal）を押す。
     ⚠️ これはダウンロード形式を選ぶ“モーダルを開くだけ”のボタン。
  4. 開いたモーダルの中の「ダウンロード」ボタンを押して初めてCSVが落ちる。
  → 164行/日 の明細CSVを取得し、Supabase sales に保存できることを確認済み。

  画面が変わって見つからない場合の保険:
    ・失敗時は debug/ に画面・HTML・入力欄/ボタン一覧・通信ログを保存する
      （実行ログにも出るので、Actionsのログだけで目印を直せる）。
    ・環境変数で目印（セレクタ）を上書きできる:
        CASHIER_DATE_FROM_SELECTOR  … 開始日の入力欄
        CASHIER_DATE_TO_SELECTOR    … 終了日の入力欄
        CASHIER_SEARCH_SELECTOR     … 検索ボタン
        CASHIER_CSV_SELECTOR        … CSV出力ボタン
"""
from __future__ import annotations

import os
import re
import time

from .browser import browser_page, dump_page
from .settings import (
    CASHIER_TRADE_URL,
    EtlError,
    RETRIES,
    RETRY_WAIT_SEC,
    require_env,
)

# ------------------------------------------------------------
# 画面の目印（候補を上から順に試す）
#
# 【2026-07-26 に実際のログイン後画面で確認】
#   ・取引履歴一覧の期間欄は「検索オプション」(class=collapsed) の中に隠れている。
#     → まず「検索オプション」を開いてから日付欄を探す。
#   ・CSV出力は2つのボタン: 「CSV出力(明細)」「CSV出力(伝票)」
#     （button.btn.btn-info.btn-sm）。欲しいのは 1明細=1行 の「明細」。
# ------------------------------------------------------------
# 「検索オプション」を開くためのラベル（閉じていることがある）
SEARCH_OPTION_TEXTS = ["検索オプション", "詳細検索", "検索条件", "条件を開く"]

DATE_FROM_CANDIDATES = [
    'input[type="date"]',
    'input[name*="start" i]',
    'input[name*="from" i]',
    'input[name*="date" i]',
    'input[class*="date" i]',
    'input.datepicker',
    'input[placeholder*="開始"]',
    'input[placeholder*="YYYY"]',
    'input[placeholder*="日付"]',
]
DATE_TO_CANDIDATES = [
    'input[type="date"]',
    'input[name*="end" i]',
    'input[name*="to" i]',
    'input[name*="date" i]',
    'input[class*="date" i]',
    'input.datepicker',
    'input[placeholder*="終了"]',
    'input[placeholder*="YYYY"]',
    'input[placeholder*="日付"]',
]
SEARCH_TEXTS = ["検索", "絞り込み", "表示", "適用", "この条件で検索"]
# 「CSV出力(明細)」を最優先。明細＝1商品1行で sales テーブルの形に合う。
CSV_TEXTS = ["CSV出力(明細)", "CSV出力（明細）", "CSVダウンロード", "CSV出力",
             "CSVエクスポート", "ダウンロード", "エクスポート", "CSV"]
# CSVの種類を選ぶメニューが出た場合、「明細」を含むものを選ぶ
DETAIL_TEXTS = ["明細", "取引明細", "商品明細", "TradeDetail"]


def _env_selector(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _first_visible(page, selectors: list[str], nth: int = 0):
    """候補を上から試して、画面に見えている最初の要素を返す。"""
    for sel in selectors:
        loc = page.locator(sel)
        try:
            count = loc.count()
        except Exception:
            continue
        if count > nth:
            target = loc.nth(nth)
            try:
                if target.is_visible():
                    return target
            except Exception:
                continue
    return None


def _click_by_text(page, texts: list[str]) -> bool:
    """ボタン／リンクの文字で探して押す。押せたら True。"""
    for text in texts:
        for role in ("button", "link", "menuitem"):
            loc = page.get_by_role(role, name=re.compile(re.escape(text)))
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    return True
            except Exception:
                continue
    # role で見つからなければ、素のテキスト一致でも試す
    for text in texts:
        loc = page.get_by_text(re.compile(re.escape(text)))
        try:
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                return True
        except Exception:
            continue
    return False


def _click_exact(page, texts: list[str]) -> bool:
    """完全一致のラベルで押す（部分一致の誤爆を避けたい時用）。押せたら True。"""
    for text in texts:
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=text, exact=True)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    return True
            except Exception:
                continue
    return False


def _click_first_text(page, texts: list[str]) -> str | None:
    """texts を上から試し、押せたらその文字列を返す（どれを押したか知りたい時用）。"""
    for text in texts:
        for role in ("button", "link", "menuitem"):
            loc = page.get_by_role(role, name=re.compile(re.escape(text)))
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    return text
            except Exception:
                continue
    return None


def open_search_options(page) -> None:
    """
    「検索オプション」（閉じていることがある）を開いて、中の期間欄を出す。
    既に開いていても押しすぎないよう、閉じている時だけ押す。
    """
    for text in SEARCH_OPTION_TEXTS:
        loc = page.get_by_text(re.compile(re.escape(text)))
        try:
            if loc.count() == 0:
                continue
            el = loc.first
            if not el.is_visible():
                continue
            # aria-expanded / class="collapsed" を見て、閉じている時だけ開く
            expanded = (el.get_attribute("aria-expanded") or "").lower()
            klass = el.get_attribute("class") or ""
            if expanded == "true" and "collapsed" not in klass:
                return  # 既に開いている
            el.click()
            page.wait_for_timeout(800)   # 展開アニメーションを待つ
            return
        except Exception:
            continue


# ------------------------------------------------------------
# ログイン
# ------------------------------------------------------------
def login(page, email: str, password: str) -> None:
    print("cashier にログインしています...")
    page.goto(CASHIER_TRADE_URL, wait_until="domcontentloaded")

    # ログイン画面へ飛ばされるのを待つ
    try:
        page.wait_for_selector('input[type="password"]', timeout=30_000)
    except Exception:
        # 既にログイン状態のまま取引一覧が出ている場合はそのまま進む
        if "/trade" in page.url:
            print("  既にログイン済みの画面が表示されました")
            return
        dump_page(page, "cashier_login_notfound")
        raise EtlError(
            "cashier のログイン画面が表示されませんでした。\n"
            f"  今のURL: {page.url}\n"
            "  → サイト側の作りが変わった可能性があります。"
            "debug/ に保存した画面を確認してください。"
        )

    page.fill('input[type="text"]', email)
    page.fill('input[type="password"]', password)
    page.click('button[type="submit"]')

    try:
        page.wait_for_url(re.compile(r"cashier\.jp/v2/client"), timeout=45_000)
    except Exception:
        dump_page(page, "cashier_login_failed")
        raise EtlError(
            "cashier にログインできませんでした。\n"
            "  よくある原因:\n"
            "    ・CASHIER_ID / CASHIER_PW（メールアドレスとパスワード）が違う\n"
            "    ・パスワードが変更された\n"
            "    ・2段階認証など、追加の確認が出ている\n"
            "  → まずご自分のブラウザで https://cashier.jp/v2/client/trade に\n"
            "     入れるか確かめてください。debug/ に失敗時の画面を保存しています。"
        )

    page.wait_for_load_state("networkidle", timeout=45_000)
    print("  ログイン成功")


# ------------------------------------------------------------
# 期間を指定する
# ------------------------------------------------------------
def set_date_range(page, business_date: str) -> None:
    """
    取引一覧の期間を「対象日 〜 対象日」にする。

    入力欄の形が type="date"（YYYY-MM-DD）か、テキスト（YYYY/MM/DD）かで
    書式が違うので、両方試す。
    """
    print(f"  期間を {business_date} に設定します")
    slash = business_date.replace("-", "/")

    # 期間欄は「検索オプション」の中に隠れていることがあるので、先に開く
    open_search_options(page)

    from_sel = _env_selector("CASHIER_DATE_FROM_SELECTOR")
    to_sel = _env_selector("CASHIER_DATE_TO_SELECTOR")

    if from_sel:
        start = page.locator(from_sel).first
        end = page.locator(to_sel).first if to_sel else None
    else:
        start = _first_visible(page, DATE_FROM_CANDIDATES, nth=0)
        end = _first_visible(page, DATE_TO_CANDIDATES, nth=1) or \
            _first_visible(page, DATE_TO_CANDIDATES, nth=0)

    if start is None:
        dump_page(page, "cashier_daterange_notfound")
        raise EtlError(
            "cashier の取引一覧で「期間」の入力欄が見つかりませんでした。\n"
            "  → debug/cashier_daterange_notfound.png を見て、\n"
            "     入力欄の目印を環境変数 CASHIER_DATE_FROM_SELECTOR /\n"
            "     CASHIER_DATE_TO_SELECTOR で指定してください。"
        )

    for field, value_pair in ((start, (business_date, slash)),
                              (end, (business_date, slash))):
        if field is None:
            continue
        for value in value_pair:
            try:
                field.fill(value)
                break
            except Exception:
                continue
        try:
            field.press("Escape")   # カレンダーが開いたら閉じる
        except Exception:
            pass

    # 検索（絞り込み）を実行。「検索」は「検索オプション」に部分一致して
    # パネルを閉じてしまう恐れがあるので、まず完全一致で押す。
    if _click_exact(page, SEARCH_TEXTS) or _click_by_text(page, ["絞り込み", "適用", "この条件で検索", "表示"]):
        try:
            page.wait_for_load_state("networkidle", timeout=45_000)
        except Exception:
            pass
    time.sleep(1)


# ------------------------------------------------------------
# CSVをダウンロードする
# ------------------------------------------------------------
def _click_csv_button(page) -> str:
    """CSV出力ボタンを押す。押した文字を返す（見つからなければ例外）。"""
    csv_sel = _env_selector("CASHIER_CSV_SELECTOR")
    if csv_sel:
        page.locator(csv_sel).first.click()
        return csv_sel
    clicked = _click_first_text(page, CSV_TEXTS)
    if clicked is None:
        dump_page(page, "cashier_csv_button_notfound")
        raise EtlError(
            "cashier の「CSV出力」ボタンが見つかりませんでした。\n"
            "  → debug/cashier_csv_button_notfound_report.txt を確認してください。"
        )
    if "明細" not in clicked:
        # 種類を選ぶメニューが出た場合だけ「明細」を選ぶ
        time.sleep(1)
        _click_by_text(page, DETAIL_TEXTS)
    return clicked


_CSV_DIAG_JS = r"""
() => {
  const hit = [...document.querySelectorAll('button, a, input')]
    .filter(e => /CSV出力/.test((e.innerText || e.value || '')));
  return hit.map(b => {
    const form = b.closest('form');
    return {
      text: (b.innerText || b.value || '').trim().slice(0, 40),
      tag: b.tagName,
      type: b.type || null,
      outerHTML: b.outerHTML.slice(0, 400),
      attrs: Object.fromEntries([...b.attributes].map(a => [a.name, a.value])),
      formAction: form ? form.getAttribute('action') : null,
      formMethod: form ? form.getAttribute('method') : null,
    };
  });
}
"""


def _diagnose_csv_buttons(page) -> None:
    """CSV出力ボタンの実体を1度だけログに出す（押下方式の特定用）。"""
    try:
        info = page.evaluate(_CSV_DIAG_JS)
    except Exception as e:
        print(f"  （CSVボタンの調査に失敗: {e}）")
        return
    import json as _json
    print("  ----8<---- CSV出力ボタンの実体 ----8<----")
    for line in _json.dumps(info, ensure_ascii=False, indent=2).splitlines():
        print(f"  | {line}")
    print("  ----8<---- ここまで ----8<----")


# CSV出力(明細)を押すと開くモーダル（ダウンロード形式の選択）
DOWNLOAD_MODAL_SELECTORS = [
    "#download-format-selector-modal",
    ".modal.show", ".modal.in",
]
# モーダル内の「実際にダウンロードする」ボタンの文字候補
MODAL_DOWNLOAD_TEXTS = ["ダウンロード", "CSVダウンロード", "ダウンロードする",
                        "出力", "CSV出力", "確定", "決定", "OK", "実行"]


def _diagnose_modal(page, modal_sel: str) -> None:
    """モーダルの中身（ボタン・入力）を1度だけログに出す。"""
    js = r"""
    (sel) => {
      const m = document.querySelector(sel);
      if (!m) return null;
      const pick = e => ({tag:e.tagName, type:e.type||null,
        text:(e.innerText||e.value||'').trim().slice(0,40),
        className:(e.className||'').toString().slice(0,80),
        name:e.name||null, id:e.id||null,
        href:e.getAttribute?e.getAttribute('href'):null});
      return {
        visible: !!(m.offsetWidth||m.offsetHeight),
        buttons: [...m.querySelectorAll('button,a,[role=button]')].map(pick),
        inputs:  [...m.querySelectorAll('input,select')].map(pick),
      };
    }
    """
    try:
        info = page.evaluate(js, modal_sel)
    except Exception as e:
        print(f"  （モーダル調査に失敗: {e}）")
        return
    if not info:
        return
    import json as _json
    print(f"  ----8<---- モーダル {modal_sel} の中身 ----8<----")
    for line in _json.dumps(info, ensure_ascii=False, indent=2).splitlines():
        print(f"  | {line}")
    print("  ----8<---- ここまで ----8<----")


def _confirm_download_modal(page) -> None:
    """
    ダウンロード形式選択モーダルが開いたら、中の実ダウンロードボタンを押す。
    モーダルが無ければ何もしない（直接ダウンロードのサイトにも耐えるように）。
    """
    modal_sel = None
    for sel in DOWNLOAD_MODAL_SELECTORS:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=8_000)
            modal_sel = sel
            break
        except Exception:
            continue
    if modal_sel is None:
        return

    print(f"  ダウンロード形式モーダルを検出: {modal_sel}")
    _diagnose_modal(page, modal_sel)

    modal = page.locator(modal_sel).first
    # モーダル内の実ダウンロードボタンを、文字候補で押す
    for text in MODAL_DOWNLOAD_TEXTS:
        try:
            btn = modal.get_by_role("button", name=re.compile(re.escape(text)))
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                print(f"  モーダル内『{text}』を押しました")
                return
        except Exception:
            continue
    # ボタンで見つからなければ、モーダル内のリンクも試す
    for text in MODAL_DOWNLOAD_TEXTS:
        try:
            lnk = modal.get_by_role("link", name=re.compile(re.escape(text)))
            if lnk.count() > 0 and lnk.first.is_visible():
                lnk.first.click()
                print(f"  モーダル内リンク『{text}』を押しました")
                return
        except Exception:
            continue
    # 最後の手段: モーダル内の primary ボタン
    try:
        prim = modal.locator(".btn-primary, button[type=submit]").first
        if prim.count() > 0 and prim.is_visible():
            prim.click()
            print("  モーダル内の主ボタン(.btn-primary)を押しました")
    except Exception:
        pass


def download_csv(page, context) -> bytes:
    """
    CSV出力ボタンを押して、ダウンロードされた中身を返す。

    cashier の「CSV出力(明細)」は別タブ（ポップアップ）で開くことがあるため、
      1) 元ページ・ポップアップ どちらのダウンロードイベントも拾う
      2) それでも来なければ、ポップアップが開いたCSVのURLを直接取りに行く
    の2段構えにしている（デジテールと同じ直接取得の考え方）。
    """
    downloads: list = []
    popups: list = []

    def _watch_page(pg) -> None:
        try:
            pg.on("download", lambda d: downloads.append(d))
        except Exception:
            pass

    _watch_page(page)
    context.on("page", lambda pg: (popups.append(pg), _watch_page(pg)))

    try:
        _click_csv_button(page)
    except EtlError:
        raise
    except Exception as e:
        dump_page(page, "cashier_csv_click_failed")
        raise EtlError(f"cashier のCSV出力ボタンを押せませんでした: {e}")

    # 「CSV出力(明細)」は data-toggle="modal" ＝ ダウンロード形式を選ぶモーダルを
    # 開くだけ。開いたモーダルの中の実ダウンロードボタンを押して初めて落ちてくる。
    _confirm_download_modal(page)

    # --- 1) ダウンロードイベントを最大90秒待つ（元ページ or ポップアップ） ---
    deadline = time.time() + 90
    while not downloads and time.time() < deadline:
        page.wait_for_timeout(500)

    if downloads:
        path = downloads[0].path()
        if path:
            return open(path, "rb").read()

    # --- 2) ダウンロードが発火しない場合: ポップアップが指すCSVのURLを直接取得 ---
    for pg in popups:
        try:
            url = pg.url
        except Exception:
            continue
        if url and any(k in url.lower() for k in ("csv", "export", "download", "/trade/")):
            resp = context.request.get(url, timeout=120_000)
            if resp.ok:
                body = resp.body()
                if body:
                    return body

    dump_page(page, "cashier_csv_download_failed")
    raise EtlError(
        "cashier のCSVダウンロードが完了しませんでした（ダウンロードもURL取得も不発）。\n"
        "  → debug/cashier_csv_download_failed_report.txt の通信記録で\n"
        "     CSV出力のURL（[popup] 行など）を確認してください。"
    )


def decode_csv(data: bytes) -> str:
    """
    文字コードを判定して文字列にする。
    日本のPOSは Shift-JIS(cp932) と UTF-8 のどちらもありうる。
    """
    for encoding in ("utf-8-sig", "cp932", "utf-8"):
        try:
            text = data.decode(encoding)
            if "伝票" in text or "明細" in text:
                return text
        except UnicodeDecodeError:
            continue
    # 判定できなければ、化けても落とさない
    return data.decode("utf-8", errors="replace")


# ------------------------------------------------------------
# まとめ
# ------------------------------------------------------------
def fetch(business_date: str, headless: bool = True) -> str:
    """
    対象日1日ぶんの売上明細CSVを取ってきて、文字列で返す。
    失敗したら間隔を空けて3回まで試す。
    """
    email = require_env("CASHIER_ID", "cashier のログイン用メールアドレス")
    password = require_env("CASHIER_PW", "cashier のログイン用パスワード")

    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with browser_page(headless=headless) as (page, context):
                login(page, email, password)
                set_date_range(page, business_date)
                return decode_csv(download_csv(page, context))
        except Exception as e:
            last_error = e
            if attempt < RETRIES:
                wait = RETRY_WAIT_SEC * attempt
                print(f"  {attempt}回目失敗（{e}）。{wait}秒待って再試行します...")
                time.sleep(wait)

    raise EtlError(f"cashier のCSVを{RETRIES}回試しても取得できませんでした。\n{last_error}")
