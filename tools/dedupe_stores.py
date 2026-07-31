"""
店舗マスタの重複をまとめる（同じ店が別レコードで登録されているのを1つに）

【なぜ必要か】
  売上（cashier / POS）と来店（デジテール）で店名の書き方が違うため、
  同じ店なのに「NOTIME天王台店」と「NOTIME天王台」のように別々の店として
  登録されてしまうことがある（スペースや「店」の有無、全角/半角のゆれ）。
  そのままだと、片方に売上・もう片方に来店…と分かれて集計がおかしくなる。

【やること】
  すべての店を「正規化キー（etl.settings.store_key）」でグループ分けし、
  同じキーの店が2つ以上あれば、いちばん正式な名前の店を“正本”にして、
  残りの売上・来店・POS接続・ログを正本へ付け替え、重複行を消す
  （実際の付け替えは DB関数 merge_store が担当。sql/013_merge_stores.sql）。

【使い方】Actions「店舗の重複をまとめる」から:
  python -m tools.dedupe_stores            # 実行（まとめる）
  python -m tools.dedupe_stores --dry-run  # 下見だけ（DBは変更しない）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etl.settings import better_store_name, load_dotenv, store_key  # noqa: E402
from etl.supabase_client import Supabase  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="下見だけ（DBは変更しない）")
    args = ap.parse_args()
    load_dotenv()

    sb = Supabase()
    stores = sb.select("stores", {"select": "*", "order": "id"})
    print(f"店舗レコード数: {len(stores)}")

    # 正規化キーでグループ分け
    groups: dict[str, list[dict]] = {}
    for s in stores:
        groups.setdefault(store_key(s["name"]), []).append(s)

    dup_groups = {k: g for k, g in groups.items() if len(g) > 1}
    if not dup_groups:
        print("✅ 重複している店舗はありませんでした。")
        return 0

    print(f"\n重複グループ: {len(dup_groups)}件"
          + ("（下見のみ・変更しません）" if args.dry_run else "") + "\n")

    merged = 0
    for key, grp in dup_groups.items():
        # 正本 = いちばん良い名前の店。表示名も全候補から一番良いものにそろえる。
        canonical = grp[0]
        best_name = grp[0]["name"]
        for s in grp:
            best_name = better_store_name(best_name, s["name"])
            if better_store_name(canonical["name"], s["name"]) == s["name"] \
                    and s["name"] != canonical["name"]:
                canonical = s

        dupes = [s for s in grp if s["id"] != canonical["id"]]
        print(f"[{key}] 正本 id={canonical['id']}『{best_name}』")
        for d in dupes:
            print(f"    ← まとめる id={d['id']}『{d['name']}』")

        if args.dry_run:
            continue

        for d in dupes:
            sb.rpc("merge_store", {"p_dupe": d["id"], "p_canonical": canonical["id"]})
            merged += 1
        if best_name != canonical["name"]:
            sb.update_store(canonical["id"], {"name": best_name})
            print(f"    → 正本の表示名を『{best_name}』にそろえました")

    if args.dry_run:
        print("\n（下見のみ）実行するには --dry-run を外してください。")
    else:
        print(f"\n✅ まとめ込み完了：重複 {merged}件を正本へ統合しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
