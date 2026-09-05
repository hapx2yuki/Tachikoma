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
