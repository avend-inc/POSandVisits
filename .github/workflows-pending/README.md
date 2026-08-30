# ここのファイルを `.github/workflows/` へ移すこと

OAuth アプリに `workflow` スコープが無く、`.github/workflows/` へ直接 push できないため、
一時的にこの場所へ置いてある。**移した時点でこのフォルダごと消えるので、移して終わり。**

## 移しかた（GitHubの画面だけで完結・1ファイル30秒）

1. このフォルダのファイルを開く
2. 鉛筆アイコン（Edit）
3. 画面上部のファイル名欄を `.github/workflows-pending/xxx.yml` →
   **`.github/workflows/xxx.yml`** に書き換える（パスごと書き換えるとGitが移動として扱う）
4. Commit changes

4本ぶん繰り返したら、この README も削除する。

## 手元のPCでやる場合

```
git mv .github/workflows-pending/daily-etl.yml       .github/workflows/daily-etl.yml
git mv .github/workflows-pending/deploy-pages.yml    .github/workflows/deploy-pages.yml
git mv .github/workflows-pending/security-audit.yml  .github/workflows/security-audit.yml
git mv .github/workflows-pending/line-sync.yml       .github/workflows/line-sync.yml
git rm .github/workflows-pending/README.md
git commit -m "ワークフローを所定の場所へ移す" && git push
```

## 中身（なぜ要るか）

| ファイル | 種類 | 中身 |
|---|---|---|
| `daily-etl.yml` | **差し替え** | 失敗時アーティファクトから `raw/`（全店の売上明細CSV）を外す。リポジトリのread権限があれば誰でも落とせる状態だった。`debug/`（POS管理画面のスクショ）の保管も 7日→1日 |
| `security-audit.yml` | 新規 | pip-audit（週1＋依存を触るPR）と compileall。脆弱性チェックの仕組みが1本も無かった |
| `line-sync.yml` | 新規 | LINE配信データの日次取り込み。既存の `DIGITAIL_ID` / `DIGITAIL_PW` を流用するので新しいSecretsは不要 |
| `deploy-pages.yml` | 差し替え | コメントのみ。gh-pages は private リポジトリでも公開配信される旨の注意書き |

`daily-etl.yml` と `deploy-pages.yml` は既存ファイルの差し替え。移動すると上書きになる。
