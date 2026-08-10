"""
ETLの異常をSlackに知らせる。

【なぜ必要か】
  日次ETLは best-effort（1つのレジ/店の取り込みが失敗しても、他の取り込みと
  ダッシュボード更新は続ける）で作ってある。おかげで全体は止まらないが、
  裏返すと Actions は緑のままなので「Airレジだけ何日も入っていない」ような
  サイレント故障に気づけない。実際に 2026-08-03〜09 のAirレジがそうなった。

【何を通知するか】
  ① 失敗したステップ（status=failed）
  ② 売上0で終わったステップ（status=no_data）… 営業している店で丸一日0件は
     たいてい取得側の異常。実際、壊れ始めのAirレジはエラーJSONを「売上0」と
     誤解釈して no_data で正常終了していた。
  ③ その営業日に売上が1件も入らなかった直営店／登録済みレジ
     … 下北沢のように1店2レジだと、片方のレジが落ちても店全体では0にならない。
        レジ単位で見ることで「Airレジだけ入っていない」を検知できる。

【通知の重複を防ぐ】
  日次ETLは予備を含めて1日4回動くので、同じ内容を何度も流さないよう
  「通知済みの印」を ingest_log に source='alert:...' で1行残す。
  ingest_log は (source, 営業日, 店舗) につき status='success' を1本しか作れない
  （sql/001_schema.sql の部分ユニークインデックス）ので、これがそのまま
  「1営業日1回だけ通知する」の仕組みになる。

【設定】
  Slack の Incoming Webhook URL を SLACK_WEBHOOK に入れる（GitHub Secrets or .env）。
  未設定なら何もしない（ログに1行出すだけ）ので、設定前でもETLは普通に動く。
"""
from __future__ import annotations

import os

import requests

from .settings import JST  # noqa: F401  （将来の時刻表示用。読み込み順の確認も兼ねる）

WEBHOOK_ENV = "SLACK_WEBHOOK"
PAGE = 1000


def _webhook() -> str:
    return (os.environ.get(WEBHOOK_ENV) or "").strip()


def _post(text: str) -> bool:
    """Slackへ1件流す。失敗してもETLは止めない（Falseを返すだけ）。"""
    url = _webhook()
    if not url:
        return False
    try:
        resp = requests.post(url, json={"text": text}, timeout=20)
        if resp.status_code >= 300:
            print(f"  （Slack通知が拒否されました: HTTP {resp.status_code} {resp.text[:120]}）")
            return False
        return True
    except Exception as e:                      # noqa: BLE001（通知失敗でETLを落とさない）
        print(f"  （Slack通知に失敗: {type(e).__name__}: {e}）")
        return False


def _alert_once(sb, key: str, business_date: str) -> bool:
    """その営業日にこの内容をまだ通知していなければ True（同時に印を残す）。

    印は ingest_log の source='alert:<key>' / status='success' に残る。
    2回目以降は部分ユニークインデックスに弾かれるので False になる。
    """
    source = f"alert:{key}"[:200]
    try:
        if sb.already_succeeded(source, business_date, None):
            return False
        sb.log(run_id="alert", source=source, business_date=business_date,
               store_id=None, status="success", message="Slack通知済み")
        return True
    except Exception as e:                      # noqa: BLE001
        print(f"  （通知済み印の記録に失敗: {type(e).__name__}: {e}）")
        return False


def _select_all(sb, table: str, select: str, extra: dict) -> list[dict]:
    """PostgRESTの1000件制限を超えて全件取る（export_dashboard と同じ考え方）。"""
    rows: list[dict] = []
    offset = 0
    while True:
        params = {"select": select, "limit": str(PAGE), "offset": str(offset)}
        params.update(extra)
        chunk = sb.select(table, params)
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        offset += PAGE
    return rows


def _missing_sales(sb, business_date: str) -> list[str]:
    """その営業日に売上が1件も入っていない「直営店」「登録済みレジ」を並べる。

    店単位だけでなくレジ単位でも見る（下北沢＝SIPOS＋Airレジのように、
    片方が落ちても店の合計は0にならないため）。
    """
    present_store: set = set()
    present_pos: set = set()
    for r in _select_all(sb, "sales", "store_id,pos_name",
                         {"business_date": f"eq.{business_date}"}):
        present_store.add(r["store_id"])
        present_pos.add((r["store_id"], (r.get("pos_name") or "").strip()))

    stores = [s for s in sb.select("stores", {"select": "*", "order": "id"})
              if s.get("id") is not None]
    name_of = {s["id"]: s.get("name") or str(s["id"]) for s in stores}

    out: list[str] = []
    for s in stores:
        # 直営で、表示対象で、もう開店している店だけを見る（FC・閉店・開店前は対象外）
        if (s.get("ownership") or "直営") != "直営":
            continue
        if not bool(s.get("visible", True)):
            continue
        od = str(s.get("open_date") or "")
        if od and od > business_date:
            continue
        if s["id"] not in present_store:
            out.append(f"{name_of[s['id']]}（店舗まるごと0件）")

    # 登録済み（active）のレジ単位。店に売上があっても、そのレジが0件なら異常。
    try:
        for p in sb.select("store_pos", {"select": "store_id,pos_name,active"}):
            if not bool(p.get("active", True)):
                continue
            pn = (p.get("pos_name") or "").strip()
            sid = p.get("store_id")
            if not pn or sid not in name_of:
                continue
            if sid not in present_store:
                continue                      # 店ごと0件は上で報告済み
            if (sid, pn) not in present_pos:
                out.append(f"{name_of[sid]} / {pn}（このレジだけ0件）")
    except Exception:
        pass                                   # store_pos が無い環境でも動くように

    return out


def notify_etl_problems(sb, business_date: str, results) -> None:
    """日次ETLの結果を見て、異常があればSlackへ1通だけ流す。

    results は run_daily の StepResult の並び（name / status / message を持つ）。
    """
    if not _webhook():
        print("  （SLACK_WEBHOOK が未設定のため、異常通知は送りません）")
        return

    lines: list[str] = []

    for r in results:
        status = getattr(r, "status", "")
        if status not in ("failed", "no_data"):
            continue
        name = getattr(r, "name", "?")
        if not _alert_once(sb, f"{status}:{name}", business_date):
            continue                            # この営業日は通知済み
        msg = (getattr(r, "message", "") or "").splitlines()
        head = msg[0][:180] if msg else ""
        label = "失敗" if status == "failed" else "売上0（取得できていない可能性）"
        lines.append(f"• {name} … {label}" + (f"\n    {head}" if head else ""))

    try:
        for miss in _missing_sales(sb, business_date):
            if _alert_once(sb, f"zero:{miss}", business_date):
                lines.append(f"• {miss}")
    except Exception as e:                      # noqa: BLE001
        print(f"  （売上0チェックに失敗: {type(e).__name__}: {e}）")

    if not lines:
        return

    text = (f":rotating_light: *NOTIME 日次ETL の異常*（営業日 {business_date}）\n"
            + "\n".join(lines)
            + "\n\nETL全体は止めずに続行しています（best-effort）。"
              "同じ営業日の同じ内容は1回だけ通知します。")
    if _post(text):
        print(f"  📣 Slackへ異常を通知しました（{len(lines)}件）。")
