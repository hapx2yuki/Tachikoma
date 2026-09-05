# タチコマ組立ガイド (作業会向けサイト)

公開 URL: https://tachikoma-build.pages.dev/

## 進捗の更新方法

1. `docs/.vitepress/data/steps.js` を編集する
   - 各作業の `status` を `todo` / `doing` / `done` に変更
   - ロードマップに「イマココ!」を出す作業に `now: true`
   - `updatedAt` と `news` (最新情報) を更新
   - `lanes[].owner` に担当者名を入れると表示される
2. ビルドしてデプロイ (環境変数の CLOUDFLARE_API_TOKEN が無効な場合は OAuth を使う)

```bash
cd site
npm run docs:build
env -u CLOUDFLARE_API_TOKEN npx wrangler pages deploy docs/.vitepress/dist --project-name=tachikoma-build --branch=main
```

## 構成

- `docs/index.md` … ダッシュボード (`Home.vue` / `Roadmap.vue`)
- `docs/steps/*.md` … 各作業ページ (図 4 : 文章 6、用語解説、完了チェック、印刷ボタン)
- `docs/.vitepress/data/glossary.js` … 用語集
- `docs/public/img/` … 図版 (SVG は手描き、PNG は `../docs/*.png` から切り出し)

## パーツリスト (`docs/parts.md`)

- データ: `docs/.vitepress/data/parts.js` (カテゴリ・数量・材料・説明・使う作業)
- STL サムネイル: `docs/public/img/parts/*.png`。再生成は`site/tools/render_parts.py` (trimesh + matplotlib, uv venv) で行った。
  形状を変えたときは該当 PNG を削除して再実行すれば差分だけ描き直す
- 電装アイコン: `docs/public/img/parts/elec-*.svg`, `mech-*.svg` (手描き SVG)
