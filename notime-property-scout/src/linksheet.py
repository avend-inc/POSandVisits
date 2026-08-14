"""検索リンク集（軽い版アウトプット）の生成。

各ページ本文の自動取得ができない環境でも「どこを見れば良いか」を1枚にまとめる。
6市 × 全ソース（ポータル/宅建協会・公的/地場）の、成功パターン向けテナント検索URLを
スマホでタップできる形（out/links.html）で出力する。§5.2で発見した実在URLを使う。

  python -m src.cli links
"""
from __future__ import annotations

import html as _html
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 市メタ（連合隊/goo等はローマ字や地方コードがサイトごとに違うので個別に持つ）
CITY_META = {
    "福島市": dict(cbiz="fukushima", homes_pref="fukushima", homes_city="fukushima",
                 jis="07201", tpref="7", city3="201", hato="07",
                 goo="touhoku_bb/area_fukushima"),
    "秋田市": dict(cbiz="akita", homes_pref="akita", homes_city="akita",
                 jis="05201", tpref="5", city3="201", hato="05",
                 goo="touhoku_bb/area_akita"),
    "富士市": dict(cbiz="shizuoka", homes_pref="shizuoka", homes_city="fuji",
                 jis="22210", tpref="22", city3="210", hato="22",
                 goo="toukai_bb/area_shizuoka"),
    "大分市": dict(cbiz="oita", homes_pref="oita", homes_city="oita",
                 jis="44201", tpref="44", city3="201", hato="44",
                 goo="kyushu_bb/area_ooita"),   # gooは「ooita」表記
    "宮崎市": dict(cbiz="miyazaki", homes_pref="miyazaki", homes_city="miyazaki",
                 jis="45201", tpref="45", city3="201", hato="45",
                 goo="kyushu_bb/area_miyazaki"),
    "長崎市": dict(cbiz="nagasaki", homes_pref="nagasaki", homes_city="nagasaki",
                 jis="42201", tpref="42", city3="201", hato="42",
                 goo="kyushu_bb/area_nagasaki"),
}


def portal_links(m: dict) -> list[tuple[str, str, str]]:
    """全国ポータルのテナント検索URL（テンプレ）。(名前, URL, 種別)"""
    return [
        ("不動産連合隊", f"https://fudosanlist.cbiz.ne.jp/list/rent/?prop=2&area={m['cbiz']}&a2={m['jis']}", "auto"),
        ("LIFULL HOME'S", f"https://www.homes.co.jp/chintai/tempo/{m['homes_pref']}/{m['homes_city']}-city/list/", "auto"),
        ("店舗ネットワーク", f"https://www.temponw.com/area_search/result?prefectures_code={m['tpref']}&cities[]={m['city3']}", "auto"),
        ("goo住宅・不動産", f"https://house.goo.ne.jp/rent/{m['goo']}/{m['jis']}.html", "auto"),
        ("ホームメイト", f"https://www.homemate.co.jp/business/pr-{m['homes_pref']}/{m['jis']}/", "auto"),
        ("ハトマークサイト", f"https://www.hatomarksite.com/search/zentaku/rent/biz/shop/area/{m['hato']}/list?m_adr[]={m['jis']}", "semi"),
    ]


# 宅建協会・公的＋地場業者は §5.2 で発見した実在URLをそのまま使う。
EXTRA_LINKS: dict[str, list[tuple[str, str, str]]] = {
    "福島市": [
        ("東日本住宅管理（地場）", "https://www.eastjapan-jk.co.jp/kasi-tenpo/fukushima/result/fukushima-city.html", "auto"),
    ],
    "秋田市": [
        ("不動産ジャパン", "https://www.fudousan.or.jp/property/rent/05/area/list?ptm[]=9303&m_adr[]=05201", "auto"),
        ("秋田市 空き店舗DB（市公式）", "https://www.city.akita.lg.jp/jigyosha/shigaichi-kasseika/1007136/index.html", "auto"),
        ("秋田住宅流通センター（地場）", "https://www.ajrc.co.jp/rent_feature/result/40097", "auto"),
        ("エクセルホーム（地場・店舗専門）", "https://excel-akita.com/", "auto"),
        ("くらサポ（地場）", "https://www.kurasapo.net/grouping/akita/%E7%A7%8B%E7%94%B0%E5%B8%82-tenant-chintai/", "auto"),
    ],
    "富士市": [
        ("丸芳不動産（地場）", "https://www.maruyosifudosan.com/sp-tenannto/", "auto"),
        ("ハートフルホームズ（地場）", "https://www.heartfulhomes.jp/kasi-tenpo/shizuoka/result/fuji-city.html", "auto"),
        ("CSA不動産（地場）", "https://csa-re.co.jp/property/?ct=1", "auto"),
        ("くらさぽ静岡（地場）", "https://www.kurasapo.net/grouping/shizuoka/%E5%AF%8C%E5%A3%AB%E5%B8%82-jigyo-chintai", "auto"),
    ],
    "大分市": [
        ("エステート三幸（地場）", "https://www.esanko.co.jp/kasi-tenpo/oita/result/oita-city.html", "auto"),
        ("林興産不動産センター（地場・355件）", "https://www.hayashi-kousan.co.jp/rent_feature/result/46963", "auto"),
        ("大分地所（地場）", "https://www.oita-jisyo.com/kasi-tenpo/oita/result/hohi-oita-eki.html", "auto"),
        ("別大興産（地場）", "https://www.betsudaikohsan.co.jp/oita-chintai/list/jgdc_city_code%5B%5D-44201/room_kind_code-3/", "auto"),
    ],
    "宮崎市": [
        ("ピラミッド（地場）", "https://www.tintai-pyramid.com/rent_feature/result/291538", "auto"),
        ("リアルエステート中央（地場）", "https://www.reaes.jp/rent_feature/result/271169", "auto"),
        ("三榮不動産（地場）", "https://www.sanei-f.com/rent_feature/result/70709", "auto"),
        ("常盤産業（地場・92件）", "https://www.tokiwasre.jp/rent_feature/result/156206", "auto"),
        ("くらサポ宮崎（地場）", "https://www.kurasapo.net/grouping/miyazaki/%E5%AE%AE%E5%B4%8E%E5%B8%82-tenant-chintai/", "auto"),
    ],
    "長崎市": [
        ("たっけんくんネット（県宅建・約900社）", "https://www.n-takken.or.jp/property/rent/area/list?ptm[]=9301&tg_adr[]=G4203", "auto"),
        ("ABC不動産（地場）", "https://www.kando-abc.com/rent_feature/result/75624", "auto"),
        ("アール不動産（地場）", "https://www.rfudosan.co.jp/kasi-tenpo/nagasaki/result/nagasaki-city.html", "auto"),
        ("ベネスト（地場）", "https://www.benestngs.co.jp/kasi-tenpo/nagasaki/result/nagasaki-city.html", "auto"),
        ("テナント長崎（地場）", "https://www.tenantnagasaki.com/", "auto"),
    ],
}


def _e(s) -> str:
    return _html.escape(str(s))


def build_html() -> str:
    sections = []
    for city, m in CITY_META.items():
        rows = []
        for name, url, kind in portal_links(m) + EXTRA_LINKS.get(city, []):
            badge = '<span class="b semi">半自動</span>' if kind == "semi" else ""
            rows.append(
                f'<a class="lk" href="{_e(url)}" target="_blank" rel="noopener">'
                f'<span class="nm">{_e(name)}{badge}</span><span class="go">開く ›</span></a>'
            )
        sections.append(
            f'<section><h2>{_e(city)} <span class="cnt">{len(rows)}件</span></h2>{"".join(rows)}</section>'
        )
    body = "".join(sections)
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>物件 検索リンク集</title>
<style>
  :root{{--ink:#1a1a1a;--sub:#666;--bd:#e4e4e4;--ac:#1558d6;}}
  *{{box-sizing:border-box}} body{{margin:0;background:#f5f5f3;color:var(--ink);
    font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN",sans-serif}}
  header{{background:#fff;border-bottom:1px solid var(--bd);padding:16px 18px;position:sticky;top:0}}
  header h1{{font-size:17px;margin:0}} header p{{font-size:12px;color:var(--sub);margin:6px 0 0;line-height:1.6}}
  main{{max-width:720px;margin:0 auto;padding:12px}}
  section{{margin:14px 0}} h2{{font-size:15px;margin:10px 4px 8px}}
  .cnt{{font-size:12px;color:var(--sub);font-weight:400}}
  .lk{{display:flex;justify-content:space-between;align-items:center;gap:10px;
    background:#fff;border:1px solid var(--bd);border-radius:11px;padding:14px 14px;margin:8px 0;
    text-decoration:none;color:var(--ink)}}
  .lk:active{{background:#eef3ff}}
  .nm{{font-size:15px;font-weight:600}} .go{{font-size:13px;color:var(--ac);white-space:nowrap}}
  .b{{font-size:10px;border-radius:6px;padding:1px 6px;margin-left:8px;vertical-align:middle}}
  .b.semi{{background:#fdecc8;color:#8a5b00}}
  footer{{color:var(--sub);font-size:12px;padding:16px;line-height:1.7}}
</style></head><body>
<header><h1>物件 検索リンク集（テナント・成功パターン向け）</h1>
<p>6市 × 取得可能ソース。タップで各サイトの<b>貸店舗・事業用テナント検索</b>が開きます。
サイト側で「駐車場あり／広い順」を足すと成功パターンに寄せられます。
<span style="color:#8a5b00">半自動</span>＝AtHome系同様bot遮断で、開いた後の取り込みは人の操作が要るもの。</p></header>
<main>{body}</main>
<footer>これは「軽い版」の出力例です。各物件の坪数・家賃・駐車場・階を自動で抜いて成功パターンで
採点する「本命」は、収集エンジンを通常回線のホストで走らせると
<code>out/YYYY-MM-DD.html</code>（カード形式）で出ます。</footer>
</body></html>"""


def write(out_dir: Path | str = ROOT / "out") -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "links.html"
    path.write_text(build_html(), encoding="utf-8")
    return path


def main():
    p = write()
    n = sum(len(portal_links(m)) + len(EXTRA_LINKS.get(c, [])) for c, m in CITY_META.items())
    print(f"検索リンク集を書き出しました: {p}")
    print(f"6市 合計 {n} リンク")
    for city, m in CITY_META.items():
        print(f"  {city}: {len(portal_links(m)) + len(EXTRA_LINKS.get(city, []))}件")


if __name__ == "__main__":
    main()
