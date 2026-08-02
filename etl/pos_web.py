"""
汎用POSレジ取得（Airレジ／EZレジ等）— 保存された接続情報でログインしCSVを取る

cashier_fetch.py と同じ考え方（ブラウザでログイン→期間指定→CSV出力）を、
アカウント（接続）ごとの URL・ID・PW を引数で受け取る汎用版にしたもの。

サイトごとに画面が違うので、目印は「候補を上から試す＋文字で探す」方式にし、
うまくいかない時は失敗地点の画面・入力欄・ボタン・通信を debug/ とログに残す
（＝最初の実運用1回で本物の目印が分かり、あとから確定できる。cashierと同じ）。

環境変数で目印を上書きできる（<PREFIX> は AIRREGI / EZREGI 等、pos_type大文字）:
  <PREFIX>_ID_SELECTOR / <PREFIX>_PW_SELECTOR / <PREFIX>_SUBMIT_TEXT
  <PREFIX>_DATE_FROM_SELECTOR / <PREFIX>_DATE_TO_SELECTOR
  <PREFIX>_SEARCH_TEXT / <PREFIX>_CSV_TEXT
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

from .browser import browser_page, dump_page, dump_controls
from .settings import EtlError, RETRIES, RETRY_WAIT_SEC
# cashier_fetch の汎用ヘルパを再利用（重複を避ける）
from .cashier_fetch import (
    _click_by_text, _click_exact, _click_first_text, _confirm_download_modal,
    _first_visible, _force_set_value, decode_csv,
)

# --- 目印の候補（サイト共通で効きやすい順）-------------------------------------
ID_CANDIDATES = ['input[type="email"]', 'input[type="text"]',
                 'input[name*="mail" i]', 'input[name*="user" i]',
                 'input[name*="login" i]', 'input[name*="id" i]']
SUBMIT_TEXTS = ["ログイン", "サインイン", "ログインする", "Login", "Sign in", "次へ"]
DATE_FROM = ['input[name="since"]', 'input[type="date"]',
             'input[name*="start" i]', 'input[name*="from" i]',
             'input[name*="date" i]', 'input[class*="date" i]',
             'input.datepicker', 'input[placeholder*="開始"]',
             'input[placeholder*="日付"]', 'input[placeholder*="YYYY"]']
DATE_TO = ['input[name="until"]', 'input[type="date"]',
           'input[name*="end" i]', 'input[name*="to" i]',
           'input[name*="date" i]', 'input[class*="date" i]',
           'input.datepicker', 'input[placeholder*="終了"]',
           'input[placeholder*="日付"]', 'input[placeholder*="YYYY"]']
SEARCH_TEXTS = ["検索", "絞り込み", "表示", "適用", "この条件で検索", "更新"]
CSV_TEXTS = ["CSV出力(明細)", "CSV出力（明細）", "CSVダウンロード", "CSV出力",
             "CSVエクスポート", "明細CSV", "ダウンロード", "エクスポート", "CSV"]


def _sel(prefix: str, name: str) -> str | None:
    v = os.environ.get(f"{prefix}_{name}", "").strip()
    return v or None


def _login(page, url: str, login_id: str, login_pw: str, prefix: str, label: str) -> None:
    print(f"  {label}: ログインしています… {url}")
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector('input[type="password"]', timeout=30_000)
    except Exception:
        # 既にログイン済み（Cookie/SSO）なら password欄は出ない
        if page.locator('input[type="password"]').count() == 0 and "login" not in page.url.lower():
            print("  （パスワード欄が無い＝既にログイン済みとみなして進みます）")
            return
        dump_page(page, f"{label}_login_notfound")
        dump_controls(page, f"{label}_login_notfound")  # ログイン欄の name もログに出す
        raise EtlError(f"{label}: ログイン画面（パスワード欄）が見つかりませんでした。URL/画面をご確認ください。")

    id_sel = _sel(prefix, "ID_SELECTOR")
    id_field = page.locator(id_sel).first if id_sel else _first_visible(page, ID_CANDIDATES)
    if id_field is not None:
        try:
            id_field.fill(login_id)
        except Exception:
            _force_set_value(page, id_sel or 'input[type="text"]', login_id)
    pw_sel = _sel(prefix, "PW_SELECTOR") or 'input[type="password"]'
    page.fill(pw_sel, login_pw)

    submit_text = _sel(prefix, "SUBMIT_TEXT")
    texts = [submit_text] if submit_text else SUBMIT_TEXTS
    if not _click_by_text(page, texts):
        try:
            page.click('button[type="submit"]')
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
    page.wait_for_timeout(2500)
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass

    # ログイン判定：パスワード欄がまだ見えていたら失敗とみなす
    pw = page.locator('input[type="password"]')
    try:
        still = pw.count() > 0 and pw.first.is_visible()
    except Exception:
        still = False
    if still:
        dump_page(page, f"{label}_login_failed")
        raise EtlError(f"{label}: ログインできませんでした（ID/PW誤り・2段階認証・画面変更など）。debug/ を確認してください。")
    print("  ログイン成功")


def _set_date_range(page, d0: str, d1: str, prefix: str, label: str) -> None:
    print(f"  {label}: 期間を {d0} 〜 {d1} に設定します")
    slash0, slash1 = d0.replace("-", "/"), d1.replace("-", "/")
    from_sel = _sel(prefix, "DATE_FROM_SELECTOR")
    to_sel = _sel(prefix, "DATE_TO_SELECTOR")
    start = page.locator(from_sel).first if from_sel else _first_visible(page, DATE_FROM, nth=0)
    end = (page.locator(to_sel).first if to_sel
           else (_first_visible(page, DATE_TO, nth=1) or _first_visible(page, DATE_TO, nth=0)))
    if start is None:
        # 期間欄が見つからなくても、全件表示のサイトもあるので致命にはしない（診断だけ残す）
        dump_page(page, f"{label}_daterange_notfound")
        dump_controls(page, f"{label}_daterange_notfound")  # 日付欄の name をログに出す
        print("  （期間欄が見つかりませんでした。全件のまま進みます。必要なら <PREFIX>_DATE_FROM_SELECTOR で指定）")
        return
    for field, pair in ((start, (d0, slash0)), (end, (d1, slash1))):
        if field is None:
            continue
        for value in pair:
            try:
                field.fill(value)
                break
            except Exception:
                continue
        try:
            field.press("Escape")
        except Exception:
            pass
    search_text = _sel(prefix, "SEARCH_TEXT")
    texts = [search_text] if search_text else SEARCH_TEXTS
    if _click_exact(page, texts) or _click_by_text(page, texts):
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
    time.sleep(1)


def _download(page, context, prefix: str, label: str) -> bytes:
    downloads: list = []
    popups: list = []

    def _watch(pg):
        try:
            pg.on("download", lambda d: downloads.append(d))
        except Exception:
            pass
    _watch(page)
    context.on("page", lambda pg: (popups.append(pg), _watch(pg)))

    csv_text = _sel(prefix, "CSV_TEXT")
    texts = [csv_text] if csv_text else CSV_TEXTS
    clicked = _click_first_text(page, texts)
    if clicked is None:
        dump_page(page, f"{label}_csv_button_notfound")
        dump_controls(page, f"{label}_csv_button_notfound")  # 隠れたCSVボタン/検索フォームもログに出す
        raise EtlError(f"{label}: 「CSV出力」ボタンが見つかりませんでした。debug/{label}_csv_button_notfound_report.txt を確認してください。")
    _confirm_download_modal(page)   # 形式選択モーダルが出たら中のダウンロードを押す

    deadline = time.time() + 90
    while not downloads and time.time() < deadline:
        page.wait_for_timeout(500)
    if downloads:
        path = downloads[0].path()
        if path:
            return open(path, "rb").read()

    # ダウンロードが発火しない場合：ポップアップのCSV URLを直接取りに行く
    for pg in popups:
        try:
            url = pg.url
        except Exception:
            continue
        if url and any(k in url.lower() for k in ("csv", "export", "download", "report")):
            resp = context.request.get(url, timeout=120_000)
            if resp.ok and resp.body():
                return resp.body()

    dump_page(page, f"{label}_csv_download_failed")
    dump_controls(page, f"{label}_csv_download_failed")
    raise EtlError(f"{label}: CSVダウンロードが完了しませんでした。debug/{label}_csv_download_failed_report.txt の通信記録をご確認ください。")


# --- SIPOS（EZレジ）専用フロー ------------------------------------------------
# 画面：ログイン → 店舗選択（既定で全店舗選択済み）→ 取引照会 → 期間指定 → 検索 → CSV。
# 実機のスクショで確定：取引照会 = /management/transaction/transactionSearch/list、
# 期間は「検索開始日時 / 検索終了日時」= YYYY/MM/DD HH:MM、結果に「CSVダウンロード」。
SIPOS_SEARCH_PATH = "/management/transaction/transactionSearch/list"


def _is_sipos(url: str) -> bool:
    return "sipos.services" in (url or "").lower()


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _sipos_normalize_url(url: str) -> str:
    """SIPOSのURL表記ゆれを正す。正解は https://<テナント>.sipos.services/management/login/。
    実データに http:// や 素のドメイン（例: http://ez-mjs.sipos.services/）が混じっており、
    そのままだと goto がタイムアウトする。https固定＋ログインパスを付ける。"""
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    host = p.netloc or p.path.split("/")[0]
    return f"https://{host}/management/login/"


def _sipos_wait(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass


def _sipos_select_all_stores(page, label: str) -> None:
    """店舗選択画面で全店舗を選ぶ。onclickのAJAXでサーバ側セッションに反映されるため、
    見た目がチェック済みでも click で必ずハンドラを発火させ、最終状態をONにする。"""
    # まず現状を診断出力（チェック状況・ボタン・選択中バナー）。
    try:
        info = page.evaluate(r"""() => {
          const q = s => [...document.querySelectorAll(s)];
          const cbs = q('input[type=checkbox]').map(c => ({id:c.id||null,
            cls:(c.className||'').toString().slice(0,50), checked:c.checked}));
          const btns = q('button,a,[role=button],input[type=submit],input[type=button]')
            .map(e => ({tag:e.tagName, text:(e.innerText||e.value||'').trim().slice(0,36),
              href:e.getAttribute&&e.getAttribute('href'),
              onclick:(e.getAttribute&&e.getAttribute('onclick'))?true:null}))
            .filter(b => b.text);
          const m = (document.body.innerText||'').match(/選択中の店舗[^\n]*/);
          return {url:location.href, banner:m?m[0]:'', cbs, btns};
        }""")
        print(f"  [SIPOS店舗選択] url={info.get('url')} / {info.get('banner')}")
        print(f"    checkbox={info.get('cbs')}")
        btns = [b for b in info.get("btns", []) if not str(b.get("href", "")).startswith("#news")]
        print(f"    button/link={btns[:20]}")
    except Exception as e:
        print(f"  （店舗選択の診断に失敗: {e}）")

    # 「全店舗選択」を OFF→ON でトグルして onclick を確実に発火させる。
    try:
        cb = page.locator("#check_all_stores").first
        if cb.count():
            if cb.is_checked():
                cb.click(); page.wait_for_timeout(400)
            cb.click(); page.wait_for_timeout(1500)
    except Exception:
        pass
    # 保険：個別の店舗チェックも未選択があれば ON にする。
    try:
        boxes = page.locator("input.store[type=checkbox]")
        for i in range(min(boxes.count(), 60)):
            b = boxes.nth(i)
            try:
                if not b.is_checked():
                    b.click(); page.wait_for_timeout(200)
            except Exception:
                continue
    except Exception:
        pass
    _sipos_wait(page)


def _sipos_set_datetime(page, label_text: str, value: str) -> bool:
    """『検索開始日時』等のラベル直後の入力欄に YYYY/MM/DD HH:MM を入れる。"""
    try:
        lbl = page.get_by_text(label_text, exact=False).first
        inp = lbl.locator("xpath=following::input[1]")
        inp.wait_for(state="visible", timeout=10_000)
    except Exception as e:
        print(f"    日時欄が見つかりません（{label_text}）: {e}")
        return False
    try:
        inp.click(timeout=5_000)
    except Exception:
        pass
    try:
        inp.fill(value, timeout=5_000)
        ok = True
    except Exception:
        # readonly / 日付ピッカーは JS で値を入れてイベントを発火させる
        try:
            h = inp.element_handle()
            page.evaluate(
                "([el,v])=>{try{el.removeAttribute('readonly');}catch(e){}"
                "el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));"
                "el.dispatchEvent(new Event('change',{bubbles:true}));}",
                [h, value])
            ok = True
        except Exception as e:
            print(f"    日時の入力に失敗（{label_text}）: {e}")
            ok = False
    try:
        page.keyboard.press("Escape")   # カレンダーを閉じる
    except Exception:
        pass
    return ok


def _sipos_open_transaction(page, base: str, label: str) -> bool:
    """メニューから『取引照会』へ遷移する。/list を直接GETするとリダイレクトされるため、
    アプリ内リンクをクリックして進む。折りたたみメニューは親を開いてから探す。"""
    def _try_click() -> bool:
        try:
            lk = page.locator("a:has-text('取引照会')")
            if lk.count():
                lk.first.click(timeout=5_000)
                _sipos_wait(page)
                return "transaction" in page.url.lower()
        except Exception:
            pass
        return False

    if _try_click():
        return True
    # 折りたたみメニュー（取引/売上/レポート等）を開いてから再挑戦。
    for grp in ("取引", "売上", "レポート", "集計", "分析", "データ"):
        try:
            g = page.locator(
                f"a:has-text('{grp}'), button:has-text('{grp}'), [data-toggle]:has-text('{grp}')"
            ).first
            if g.count():
                g.click(timeout=2_000)
                page.wait_for_timeout(600)
                if _try_click():
                    return True
        except Exception:
            continue
    # 最後の手段：リンクの href を取り出して直接遷移する。
    try:
        lk = page.locator("a:has-text('取引照会')")
        if lk.count():
            href = lk.first.get_attribute("href")
            if href:
                target = href if href.startswith("http") else base + href
                page.goto(target, wait_until="domcontentloaded")
                _sipos_wait(page)
                return "transaction" in page.url.lower()
    except Exception:
        pass
    return False


def _sipos_open_report(page, base: str, label: str) -> bool:
    """メニューから『売上報告書』（販売管理→売上報告書）へ遷移する。到達判定は
    『CSVダウンロード』ボタンが出るかどうかで行う（URLはテナントで異なるため）。"""
    def _has_csv_button() -> bool:
        try:
            return page.locator(
                "button:has-text('CSVダウンロード'), a:has-text('CSVダウンロード'),"
                "button:has-text('CSV'), a:has-text('CSV')").count() > 0
        except Exception:
            return False

    def _try_click() -> bool:
        try:
            lk = page.locator("a:has-text('売上報告書'), button:has-text('売上報告書')")
            if lk.count():
                lk.first.click(timeout=5_000)
                _sipos_wait(page)
                return _has_csv_button()
        except Exception:
            pass
        return False

    if _try_click():
        return True
    # 折りたたみメニュー（販売管理/売上/レポート等）を開いてから再挑戦。
    for grp in ("販売管理", "売上", "販売", "レポート", "集計", "分析", "データ"):
        try:
            g = page.locator(
                f"a:has-text('{grp}'), button:has-text('{grp}'), [data-toggle]:has-text('{grp}')"
            ).first
            if g.count():
                g.click(timeout=2_000)
                page.wait_for_timeout(600)
                if _try_click():
                    return True
        except Exception:
            continue
    # 最後の手段：リンクの href を取り出して直接遷移する。
    try:
        lk = page.locator("a:has-text('売上報告書')")
        if lk.count():
            href = lk.first.get_attribute("href")
            if href:
                target = href if href.startswith("http") else base + href
                page.goto(target, wait_until="domcontentloaded")
                _sipos_wait(page)
                return _has_csv_button()
    except Exception:
        pass
    return False


def _sipos_check_option(page, label_text: str) -> bool:
    """ダウンロード形式モーダルの中で、ラベル文字に対応するチェックボックスをONにする。
    レイアウトが『□ ラベル』でも『ラベル □』でも拾えるよう、各チェックボックスの
    近傍テキスト（for属性ラベル/囲みラベル/親・隣接テキスト）で照合する。"""
    js = r"""
    (txt) => {
      const cbs = [...document.querySelectorAll('input[type=checkbox]')];
      const near = (cb) => {
        let s = '';
        if (cb.id){ const l=document.querySelector('label[for="'+cb.id+'"]'); if(l) s+=' '+l.innerText; }
        const wl = cb.closest('label'); if (wl) s += ' ' + wl.innerText;
        if (cb.parentElement) s += ' ' + cb.parentElement.innerText;
        if (cb.nextElementSibling) s += ' ' + cb.nextElementSibling.innerText;
        if (cb.previousElementSibling) s += ' ' + cb.previousElementSibling.innerText;
        return s;
      };
      for (const cb of cbs){
        if (near(cb).includes(txt)){ if(!cb.checked){ cb.click(); } return true; }
      }
      return false;
    }"""
    try:
        return bool(page.evaluate(js, label_text))
    except Exception:
        return False


def _fetch_sipos(page, context, url: str, d0: str, d1: str, label: str) -> bytes:
    """SIPOS：ログイン済みページから 売上報告書 → CSVダウンロード →
    『購買情報明細』を選んでダウンロード → ZIP を展開し purchaseDetailsList_*.csv を返す。

    取引照会（取引単位・明細なし）ではなく、売上報告書の購買情報明細（＝Si明細と同形式：
    店舗コード/商品分類名/税込売価…）を使う。カテゴリ別も出せ、売上・客数・点数が
    売上報告書に一致する。"""
    base = _origin(url)
    debug = bool(os.environ.get("POS_DEBUG") or os.environ.get("POS_ONLY_STORE"))

    # 1) 店舗選択（サーバ側セッションに全店を反映）。ログイン直後は storeSelect の想定。
    if "storeselect" not in page.url.lower():
        try:
            page.goto(base + "/management/storeSelect", wait_until="domcontentloaded")
            _sipos_wait(page)
        except Exception:
            pass
    _sipos_select_all_stores(page, label)

    # 2) 売上報告書へアプリ内遷移。
    page.goto(base + "/management/", wait_until="domcontentloaded")
    _sipos_wait(page)
    if not _sipos_open_report(page, base, label):
        dump_page(page, f"{label}_sipos_report_notfound")
        dump_controls(page, f"{label}_sipos_report_notfound")
        raise EtlError(f"{label}: 売上報告書メニューへ移動できませんでした。")
    if debug:
        print(f"  {label}: 売上報告書の到達URL = {page.url}")

    # 3) CSVダウンロードを開く（形式選択モーダルが出る）。
    downloads: list = []
    page.on("download", lambda d: downloads.append(d))
    context.on("page", lambda pg: pg.on("download", lambda d: downloads.append(d)))

    if _click_first_text(page, ["CSVダウンロード", "CSV出力", "CSVエクスポート", "CSV"]) is None:
        dump_page(page, f"{label}_sipos_csvbtn_notfound")
        dump_controls(page, f"{label}_sipos_csvbtn_notfound")
        raise EtlError(f"{label}: 売上報告書の「CSVダウンロード」ボタンが見つかりませんでした。")
    page.wait_for_timeout(1200)   # モーダルの描画待ち

    # 4) 期間（開始日/終了日 = YYYY/MM/DD）をモーダルに設定。
    start_v = d0.replace("-", "/")
    end_v = d1.replace("-", "/")
    print(f"  {label}: ダウンロード期間を {start_v} 〜 {end_v} に設定します")
    _sipos_set_datetime(page, "開始日", start_v)
    _sipos_set_datetime(page, "終了日", end_v)

    # 5) 『購買情報明細』にチェック（他はそのままでもZIP内から明細だけ拾う）。
    if not _sipos_check_option(page, "購買情報明細"):
        # 表記ゆれ（購入情報明細）にも対応。
        _sipos_check_option(page, "購入情報明細")
    if debug:
        dump_controls(page, f"{label}_sipos_download_modal")

    # 6) モーダル内の『ダウンロード』を押す。
    _confirm_download_modal(page)

    deadline = time.time() + 120
    while not downloads and time.time() < deadline:
        page.wait_for_timeout(500)
    if not downloads:
        dump_page(page, f"{label}_sipos_download_failed")
        dump_controls(page, f"{label}_sipos_download_failed")
        raise EtlError(f"{label}: ダウンロードが完了しませんでした。debug/ の記録をご確認ください。")
    path = downloads[0].path()
    if not path:
        raise EtlError(f"{label}: ダウンロードのパスが取得できませんでした。")
    data = open(path, "rb").read()
    print(f"  {label}: ダウンロード取得（{len(data)} bytes）")
    return _extract_purchase_details(data, label)


def _extract_purchase_details(data: bytes, label: str) -> bytes:
    """ダウンロードが ZIP なら purchaseDetailsList_*.csv を取り出す。CSVならそのまま。"""
    if not data:
        return b""
    if data[:2] == b"PK":
        import io as _io
        import zipfile
        try:
            zf = zipfile.ZipFile(_io.BytesIO(data))
        except Exception as e:
            raise EtlError(f"{label}: ZIPを展開できませんでした: {e}")
        names = zf.namelist()
        target = next((n for n in names if "purchasedetail" in n.lower()), None)
        if target is None:
            target = next((n for n in names if n.lower().endswith(".csv")), None)
        print(f"  {label}: ZIP内 {names} → 使用: {target}")
        if target is None:
            raise EtlError(f"{label}: ZIPに購買情報明細(CSV)が見つかりません: {names}")
        return zf.read(target)
    return data


def _decode_sipos(data: bytes) -> str:
    """購買情報明細CSVの文字コード（UTF-8 BOM / UTF-8 / Shift-JIS）を判定して文字列化。"""
    if not data:
        return ""
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def fetch(url: str, login_id: str, login_pw: str, business_date: str,
          pos_type: str, label: str = "pos", headless: bool = True,
          end_date: str | None = None) -> str:
    """接続情報でログイン→期間指定→CSV出力し、CSV文字列を返す。失敗時は数回試す。"""
    if not url:
        raise EtlError(f"{label}: URLが未登録です（管理画面のレジ接続でURLを設定してください）。")
    # SIPOSは http:// や素のドメイン表記を正す（https＋/management/login/）。
    if _is_sipos(url):
        url = _sipos_normalize_url(url)
    d1 = end_date or business_date
    prefix = (pos_type or "pos").upper()
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with browser_page(headless=headless) as (page, context):
                _login(page, url, login_id or "", login_pw or "", prefix, label)
                if _is_sipos(url):
                    return _decode_sipos(_fetch_sipos(page, context, url, business_date, d1, label))
                _set_date_range(page, business_date, d1, prefix, label)
                return decode_csv(_download(page, context, prefix, label))
        except Exception as e:
            last = e
            if attempt < RETRIES:
                wait = RETRY_WAIT_SEC * attempt
                print(f"  {label}: {attempt}回目失敗（{e}）。{wait}秒待って再試行します…")
                time.sleep(wait)
    raise EtlError(f"{label}: CSVを{RETRIES}回試しても取得できませんでした。\n{last}")
