"""名簿（app_users / users / app_user_stores）の状態を調べる（読み取りのみ）。

    python -m tools.auth_probe

【何のため】
  売上アプリは app_users、在庫アプリは users という別々の名簿を持っている。
  これを1枚にまとめる前に、いま誰がどちらに居て、加盟店の割り当てが
  どれだけ有るのかをはっきりさせる。

【メールアドレスは出さない】
  このリポジトリは公開されていて、Actionsのログも公開になる。
  個人のアドレスをログに残さないため、ドメインと件数だけを出す。
  （@より前は伏せる。誰が居るかはSupabaseの画面で見る）
"""
from __future__ import annotations

import os
import re
import sys

import requests

URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
TOKEN = (os.environ.get("SUPABASE_ACCESS_TOKEN") or "").strip()
REF = re.sub(r"^https?://([^.]+)\..*$", r"\1", URL) if URL else ""


def run(sql: str):
    if not REF or not TOKEN:
        raise SystemExit("SUPABASE_URL / SUPABASE_ACCESS_TOKEN が設定されていません。")
    r = requests.post(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": sql}, timeout=60)
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def ask(title: str, sql: str) -> None:
    print(f"\n===== {title} =====")
    try:
        rows = run(sql)
    except SystemExit as e:
        print(f"  ⚠️ 取れませんでした: {str(e)[:300]}")
        return
    if not rows:
        print("  （該当なし）")
        return
    cols = list(rows[0].keys())
    print("  " + " | ".join(cols))
    for r in rows:
        print("  " + " | ".join(str(r.get(c)) for c in cols))


def main() -> int:
    print("=" * 60)
    print("名簿の状態（app_users / users / app_user_stores）")
    print("=" * 60)

    ask("① 売上アプリの名簿 app_users（ドメイン別）", """
        select split_part(email,'@',2) as ドメイン,
               coalesce(role,'(なし)') as ロール,
               count(*) as 人数
          from app_users group by 1,2 order by 3 desc
    """)

    ask("② 在庫アプリの名簿 users（ドメイン別）", """
        select split_part(email,'@',2) as ドメイン,
               coalesce(role,'(なし)') as ロール,
               is_active as 有効,
               count(*) as 人数
          from users group by 1,2,3 order by 4 desc
    """)

    ask("③ 両方に居る／片方だけ", """
        select case
                 when a.email is not null and u.email is not null then '両方に居る'
                 when a.email is not null then 'app_usersだけ（売上アプリのみ）'
                 else 'usersだけ（在庫アプリのみ）'
               end as 状態,
               count(*) as 人数
          from app_users a
          full outer join users u on lower(u.email) = lower(a.email)
         group by 1 order by 2 desc
    """)

    ask("④ 加盟店の割り当て app_user_stores", """
        select count(*) as 行数,
               count(distinct email) as 人数,
               count(distinct store_id) as 店舗数
          from app_user_stores
    """)

    ask("⑤ 割り当てを持つ人のドメイン", """
        select split_part(s.email,'@',2) as ドメイン,
               count(distinct s.email) as 人数,
               count(*) as 割当行数,
               bool_or(a.email is not null) as app_usersに居る,
               bool_or(u.email is not null) as usersに居る
          from app_user_stores s
          left join app_users a on lower(a.email) = lower(s.email)
          left join users     u on lower(u.email) = lower(s.email)
         group by 1 order by 3 desc
    """)

    ask("⑥ users テーブルの列", """
        select column_name as 列, data_type as 型, is_nullable as 空を許すか
          from information_schema.columns
         where table_schema='public' and table_name='users'
         order by ordinal_position
    """)

    ask("⑦ app_users テーブルの列", """
        select column_name as 列, data_type as 型, is_nullable as 空を許すか
          from information_schema.columns
         where table_schema='public' and table_name='app_users'
         order by ordinal_position
    """)

    ask("⑧ app_users / is_hq / current_app_* に依存しているRLSポリシー", """
        select schemaname as スキーマ, tablename as テーブル, policyname as ポリシー
          from pg_policies
         where schemaname='public'
           and (coalesce(qual,'') || coalesce(with_check,'')) ~*
               'app_users|is_hq|current_app_role|current_app_email|app_user_stores'
         order by 2,3
    """)

    ask("⑨ 依存している関数", """
        select p.proname as 関数
          from pg_proc p
          join pg_namespace n on n.oid = p.pronamespace
         where n.nspname='public'
           and pg_get_functiondef(p.oid) ~* 'app_users|app_user_stores'
         order by 1
    """)
    return 0


if __name__ == "__main__":
    sys.exit(main())
