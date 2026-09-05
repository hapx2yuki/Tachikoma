import { defineConfig } from 'vitepress'
import { steps, lanes } from './data/steps.js'

const sidebar = [
  { text: 'ダッシュボード', link: '/' },
  { text: 'はじめに (全員必読)', link: '/guide/start' },
  ...lanes.map(l => ({
    text: `${l.icon} ${l.name}`,
    collapsed: false,
    items: steps.filter(s => s.lane === l.id).map(s => ({ text: `${s.id.toUpperCase()} ${s.title}`, link: `/steps/${s.id}` }))
  })),
  { text: '用語集', link: '/glossary' },
]

export default defineConfig({
  title: 'タチコマ組立ガイド',
  description: 'タチコマ歩行ロボット 物理製作フェーズの作業ガイドと進捗ダッシュボード',
  lang: 'ja-JP',
  cleanUrls: true,
  appearance: false,
  head: [
    ['link', { rel: 'preconnect', href: 'https://fonts.googleapis.com' }],
    ['link', { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' }],
    ['link', { rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=Reggae+One&family=Yusei+Magic&family=Zen+Kaku+Gothic+New:wght@400;500;700;900&display=swap' }],
    ['link', { rel: 'icon', href: '/favicon.svg' }],
  ],
  themeConfig: {
    logo: '/favicon.svg',
    nav: [
      { text: 'ダッシュボード', link: '/' },
      { text: 'はじめに', link: '/guide/start' },
      { text: '用語集', link: '/glossary' },
      { text: 'GitHub', link: 'https://github.com/hapx2yuki/Tachikoma' },
    ],
    sidebar,
    outline: { label: 'このページ', level: [2, 3] },
    docFooter: { prev: '前の作業', next: '次の作業' },
    sidebarMenuLabel: 'メニュー',
    returnToTopLabel: 'トップへ',
    lastUpdated: false,
    search: { provider: 'local', options: { translations: { button: { buttonText: '検索' } } } },
  },
})
