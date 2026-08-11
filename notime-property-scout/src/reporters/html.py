"""日次HTMLレポート（§9.2・主出力）。out/YYYY-MM-DD.html に単一ファイルで出力。

CSSはインライン。ブラウザで開くだけで完結（サーバ不要）。
- Aランク・Bランクを上に、Cランクは折りたたみ、要確認は末尾に別枠
- 1物件1カード（§9.1の9列を主表示）＋ AI評価バッジ＋フラグ＋補助情報（折りたたみ）
- ヘッダに当日サマリ（収集/ゲート通過/新規/broken）
- カード単位で「TSVをコピー」ボタン
"""
from __future__ import annotations

import html as _html
from datetime import date
from pathlib import Path

from ..models import Property
from .base import RunSummary
from .columns import (
    col_ai, col_area, col_found_date, col_gmap_link, col_name, col_parking,
    col_rent, col_station, row9,
)

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "out"

_VERDICT_CLASS = {"成功店類似": "ok", "失敗店類似": "ng", "判定不可": "unknown"}
_RANK_ORDER = {"A": 0, "B": 1, "C": 2, "要確認": 3, "除外": 4}


def _e(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _card(p: Property) -> str:
    vclass = _VERDICT_CLASS.get(p.location_verdict, "unknown")
    flags_html = "".join(f'<span class="flag">{_e(f)}</span>' for f in p.flags)
    tsv = "\t".join(str(c).replace("\t", " ").replace("\n", " ") for c in row9(p))
    tsv_attr = _e(tsv)

    # 補助情報（折りたたみ・§9.1 / §9.2）: 内部スコア・ランク・想定月商・接道・チェーン数・取得費
    est = "—"
    if p.est_sales_mid is not None:
        est = (f"低 ¥{p.est_sales_low:,} / 中 ¥{p.est_sales_mid:,} / 高 ¥{p.est_sales_high:,}")
    aux_rows = [
        ("内部スコア", "—" if p.score is None else f"{p.score}点"),
        ("ランク", p.rank),
        ("想定月商レンジ", est),
        ("接道クラス", p.road_class or "未取得（第2段階）"),
        ("500m内チェーン飲食", "未取得" if p.chain_restaurants_500m is None else p.chain_restaurants_500m),
        ("セカスト2km内", "未取得" if p.secondstreet_within_2km is None else ("有" if p.secondstreet_within_2km else "無")),
        ("物件取得費", "—" if p.acquisition_cost is None else f"¥{p.acquisition_cost:,}"),
        ("坪単価", "—" if not (p.rent_monthly and p.area_tsubo) else f"¥{p.rent_monthly / p.area_tsubo:,.0f}/坪"),
        ("ゲート", p.gate_result + ("：" + " / ".join(p.gate_failed_on) if p.gate_failed_on else "")),
    ]
    aux = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in aux_rows)

    return f"""
    <div class="card rank-{_e(p.rank)}">
      <div class="badge {vclass}">{_e(col_ai(p))}</div>
      <div class="head">
        <span class="found">見つけた日 {_e(col_found_date(p))}</span>
        <span class="rank">{_e(p.rank)}</span>
      </div>
      <div class="name">{_e(col_name(p))}</div>
      <div class="flags">{flags_html}</div>
      <div class="specs">
        <div><label>坪数</label><b>{_e(col_area(p))}</b></div>
        <div><label>家賃</label><b>{_e(col_rent(p))}</b></div>
        <div><label>駐車場</label><b>{_e(col_parking(p))}</b></div>
        <div><label>主要駅</label><b>{_e(col_station(p))}</b></div>
      </div>
      <div class="links">
        <a href="{_e(p.detail_url)}" target="_blank" rel="noopener">物件ページ</a>
        <a href="{_e(col_gmap_link(p))}" target="_blank" rel="noopener">Googleマップ</a>
        <button class="copytsv" data-tsv="{tsv_attr}">TSVをコピー</button>
      </div>
      <details class="aux"><summary>補助情報</summary>
        <table>{aux}</table>
      </details>
    </div>"""


def _section(title: str, props: list[Property], collapsed: bool = False) -> str:
    if not props:
        return ""
    cards = "".join(_card(p) for p in props)
    if collapsed:
        return f'<details class="section"><summary>{_e(title)}（{len(props)}件）</summary>{cards}</details>'
    return f'<section><h2>{_e(title)}（{len(props)}件）</h2>{cards}</section>'


class HtmlReporter:
    def __init__(self, out_dir: Path | str = OUT_DIR):
        self.out_dir = Path(out_dir)

    def emit(self, properties: list[Property], run_date: date, summary: RunSummary):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{run_date.isoformat()}.html"

        ordered = sorted(
            properties,
            key=lambda p: (_RANK_ORDER.get(p.rank, 9), -(p.score or 0)),
        )
        ab = [p for p in ordered if p.rank in ("A", "B")]
        c = [p for p in ordered if p.rank == "C"]
        need = [p for p in ordered if p.rank == "要確認"]
        excluded = [p for p in ordered if p.rank == "除外"]

        broken = ", ".join(summary.broken_sources) if summary.broken_sources else "なし"
        body = (
            _section("A・Bランク", ab)
            + _section("Cランク", c, collapsed=True)
            + _section("要確認", need, collapsed=True)
            + _section("除外（ゲート不通過）", excluded, collapsed=True)
        )

        doc = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>物件レポート {run_date.isoformat()}</title>
<style>
  :root {{ --ok:#1b8a3a; --ng:#c0392b; --unknown:#777; --border:#e2e2e2; --ink:#222; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN",sans-serif; color:var(--ink); margin:0; background:#f6f6f4; }}
  header {{ background:#fff; border-bottom:1px solid var(--border); padding:16px 18px; position:sticky; top:0; z-index:5; }}
  header h1 {{ font-size:18px; margin:0 0 6px; }}
  .summary {{ font-size:13px; color:#555; display:flex; gap:14px; flex-wrap:wrap; }}
  .summary b {{ color:var(--ink); }}
  main {{ max-width:760px; margin:0 auto; padding:14px; }}
  section, details.section {{ margin:14px 0; }}
  h2 {{ font-size:15px; margin:16px 4px 8px; }}
  .card {{ background:#fff; border:1px solid var(--border); border-radius:12px; padding:14px; margin:10px 0; box-shadow:0 1px 3px rgba(0,0,0,.05); }}
  .badge {{ display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:999px; color:#fff; margin-bottom:8px; }}
  .badge.ok {{ background:var(--ok); }} .badge.ng {{ background:var(--ng); }} .badge.unknown {{ background:var(--unknown); }}
  .head {{ display:flex; justify-content:space-between; font-size:12px; color:#666; }}
  .head .rank {{ font-weight:700; color:var(--ink); }}
  .name {{ font-size:16px; font-weight:700; margin:4px 0; }}
  .flags {{ display:flex; gap:6px; flex-wrap:wrap; margin:4px 0; }}
  .flag {{ font-size:11px; background:#fdecc8; color:#8a5b00; border-radius:6px; padding:2px 7px; }}
  .specs {{ display:grid; grid-template-columns:1fr 1fr; gap:6px 12px; margin:8px 0; }}
  .specs label {{ display:block; font-size:11px; color:#888; }}
  .specs b {{ font-size:15px; }}
  .links {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; align-items:center; }}
  .links a {{ font-size:13px; text-decoration:none; color:#1558d6; border:1px solid #ccd; border-radius:8px; padding:6px 10px; }}
  .copytsv {{ font-size:12px; border:1px solid var(--border); background:#fafafa; border-radius:8px; padding:6px 10px; cursor:pointer; }}
  details.aux {{ margin-top:10px; }}
  details.aux summary {{ font-size:12px; color:#666; cursor:pointer; }}
  details.aux table {{ width:100%; border-collapse:collapse; margin-top:6px; font-size:12px; }}
  details.aux th {{ text-align:left; color:#888; font-weight:600; padding:3px 6px; width:42%; white-space:nowrap; }}
  details.aux td {{ padding:3px 6px; }}
  details.section > summary {{ font-size:15px; font-weight:700; cursor:pointer; padding:6px 4px; }}
</style></head>
<body>
<header>
  <h1>物件レポート <span style="font-weight:400">{run_date.isoformat()}</span></h1>
  <div class="summary">
    <span>収集 <b>{summary.collected}</b> 件</span>
    <span>ゲート通過 <b>{summary.gate_pass}</b> 件</span>
    <span>新規 <b>{summary.new_count}</b> 件</span>
    <span>broken ソース: <b>{_e(broken)}</b></span>
  </div>
</header>
<main>{body or '<p style="color:#888;padding:20px">対象物件がありません。</p>'}</main>
<script>
  document.querySelectorAll('.copytsv').forEach(function(b){{
    b.addEventListener('click', function(){{
      navigator.clipboard.writeText(b.dataset.tsv).then(function(){{
        var t=b.textContent; b.textContent='コピーしました'; setTimeout(function(){{b.textContent=t;}},1200);
      }});
    }});
  }});
</script>
</body></html>"""
        path.write_text(doc, encoding="utf-8")
        return path
