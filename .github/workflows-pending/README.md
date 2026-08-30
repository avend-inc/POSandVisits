# ここのファイルを `.github/workflows/` へ移すこと

OAuth アプリに `workflow` スコープが無く、`.github/workflows/` へ直接 push できないため、
一時的にこの場所へ置いてある。移し終えたらこのフォルダごと消すこと。

**新規2本と差し替え2本で、やることが違う。** 下記のとおり。

---

## A. 新規の2本 — リネームするだけ

`security-audit.yml` / `line-sync.yml`

1. ファイルを開く → 鉛筆アイコン（Edit）
2. 画面上部のファイル名欄を `.github/workflows-pending/xxx.yml` →
   **`.github/workflows/xxx.yml`** に書き換える（パスごと書き換えるとGitが移動として扱う）
3. Commit changes

## B. 差し替えの2本 — 既存ファイルを直接いじる

`daily-etl.yml` / `deploy-pages.yml` は同名のファイルが `.github/workflows/` に既にあり、
**そこへのリネームはGitHubの画面で弾かれる**（A の手順は使えない）。
既存ファイルを開いて、下記のとおり直すのが早い。

### `.github/workflows/daily-etl.yml`（急ぐのはこれ）

いちばん下、182〜192行目あたりを丸ごと差し替える。

**いま:**
```yaml
      # 失敗したときだけ、原因調査用のファイルを残す（7日で自動削除）
      - name: 失敗時の調査用ファイルを保存
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: debug-${{ github.run_id }}
          path: |
            debug/
            raw/
          if-no-files-found: ignore
          retention-days: 7
```

**こう:**
```yaml
      # 失敗したときだけ、原因調査用のファイルを残す。
      # raw/ は取得した生CSV＝全店の売上明細そのもの。画面調査には要らないのに
      # リポジトリのread権限がある人なら誰でも落とせる状態だったので外した
      # （生CSVが要るときは Actions のログか、手元で --headed で再現する）。
      # debug/ もPOS管理画面のスクリーンショットが入るため、保管は1日だけにする。
      - name: 失敗時の調査用ファイルを保存
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: debug-${{ github.run_id }}
          path: debug/
          if-no-files-found: ignore
          retention-days: 1
```

変えているのは3つ。`path:` から `raw/` を落とす／`|` の複数行をやめて `debug/` 1行にする／
`retention-days` を 7 → 1。

### `.github/workflows/deploy-pages.yml`

コメントだけなので急がない。21行目（`# ============` の行）の直前に足す:

```
#  ⚠️ GitHub Pages はリポジトリが private でも公開配信される。
#     売上アプリは在庫アプリのドメイン(/sales/)へ移したので、並べての確認が
#     終わったらこのワークフローごと畳み、Pages を無効化して gh-pages を消すこと。
#     畳む前に必ず一度この配信を通すこと（古い版に実データが焼き込まれているため、
#     先に止めると公開されたままになる）。
```

---

## 手元のPCでやる場合（こちらが確実・全部まとめて）

```
git mv -f .github/workflows-pending/daily-etl.yml       .github/workflows/daily-etl.yml
git mv -f .github/workflows-pending/deploy-pages.yml    .github/workflows/deploy-pages.yml
git mv    .github/workflows-pending/security-audit.yml  .github/workflows/security-audit.yml
git mv    .github/workflows-pending/line-sync.yml       .github/workflows/line-sync.yml
git rm    .github/workflows-pending/README.md
git commit -m "ワークフローを所定の場所へ移す" && git push
```

`git mv -f` なら既存ファイルの上書きも通るので、Bの手編集は要らない。

---

## 中身（なぜ要るか）

| ファイル | 種類 | 中身 |
|---|---|---|
| `daily-etl.yml` | **差し替え** | 失敗時アーティファクトから `raw/`（全店の売上明細CSV）を外す。リポジトリのread権限があれば誰でも落とせる状態だった。`debug/`（POS管理画面のスクショ）の保管も 7日→1日 |
| `security-audit.yml` | 新規 | pip-audit（週1＋依存を触るPR）と compileall。脆弱性チェックの仕組みが1本も無かった |
| `line-sync.yml` | 新規 | LINE配信データの日次取り込み。既存の `DIGITAIL_ID` / `DIGITAIL_PW` を流用するので新しいSecretsは不要 |
| `deploy-pages.yml` | 差し替え | コメントのみ。gh-pages は private リポジトリでも公開配信される旨の注意書き |
