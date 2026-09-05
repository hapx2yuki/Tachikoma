<script setup>
import { computed } from 'vue'
import { steps, lanes, news, updatedAt } from '../../data/steps.js'
import Roadmap from './Roadmap.vue'

const total = steps.length
const done = computed(() => steps.filter(s => s.status === 'done').length)
const doing = computed(() => steps.filter(s => s.status === 'doing'))
const minutesAll = steps.reduce((a, s) => a + s.minutes, 0)
const minutesDone = computed(() => steps.filter(s => s.status === 'done').reduce((a, s) => a + s.minutes, 0))
const pct = computed(() => Math.round((minutesDone.value / minutesAll) * 100))
const st = { todo: '未着手', doing: '作業中', done: '完了', blocked: '停止' }
const laneSteps = id => steps.filter(s => s.lane === id)
const ready = computed(() => steps.filter(s => s.status === 'todo' && s.deps.every(d => (steps.find(x => x.id === d) || {}).status === 'done')))
</script>

<template>
  <div class="home">
    <section class="hero">
      <div class="hero-in">
        <p class="kicker">TACHIKOMA WALKER BUILD — 物理製作フェーズ</p>
        <h1>タチコマ<br />組立ガイド</h1>
        <p class="lead">3D プリントは全部終わった。ここからは <b>組んで、つないで、歩かせる</b>。<br />電子工作が初めてでも進められるように、作業ごとに図と用語解説を付けています。</p>
        <div class="cta">
          <a class="btn primary" href="#roadmap">ロードマップを見る</a>
          <a class="btn" href="/guide/start">はじめに (全員必読)</a>
          <a class="btn" href="/glossary">用語集</a>
        </div>
      </div>
      <div class="hero-art">
        <img src="/img/preview_robot.png" alt="タチコマ骨格の 3 面図" />
        <span class="tag">設計は完了 · MuJoCo 物理シムで歩行実証済み</span>
      </div>
    </section>

    <section class="progress">
      <div class="pbox">
        <div class="ptop"><span class="plabel">全体の進捗</span><span class="pnum">{{ pct }}<small>%</small></span><span class="pcount">{{ done }} / {{ total }} 作業クリア</span><span class="pupd">更新: {{ updatedAt }}</span></div>
        <div class="bar"><div class="fill" :style="{ width: pct + '%' }"></div><div v-for="s in doing" :key="s.id" class="doing-mark" :style="{ left: pct + '%' }"></div></div>
        <div class="now-row">
          <div class="now-cards">
            <div class="now-h">📍 イマココ (作業中)</div>
            <a v-for="s in doing" :key="s.id" :href="'/steps/' + s.id" class="now-card" :style="{ '--c': lanes.find(l => l.id === s.lane).color }">
              <span class="ic">{{ s.icon }}</span><span><b>{{ s.id.toUpperCase() }} {{ s.title }}</b><br /><small>{{ s.summary }}</small></span>
            </a>
            <p v-if="!doing.length" class="muted">作業中の項目はありません。</p>
          </div>
          <div class="now-cards">
            <div class="now-h">🟢 いますぐ始められる</div>
            <a v-for="s in ready" :key="s.id" :href="'/steps/' + s.id" class="now-card" :style="{ '--c': lanes.find(l => l.id === s.lane).color }">
              <span class="ic">{{ s.icon }}</span><span><b>{{ s.id.toUpperCase() }} {{ s.title }}</b><br /><small>約 {{ s.minutes }} 分 · 前提なし</small></span>
            </a>
            <p v-if="!ready.length" class="muted">前提待ちの作業のみです。</p>
          </div>
        </div>
      </div>
    </section>

    <section id="roadmap" class="roadmap">
      <h2 class="sec-h"><span>🗺️</span> 製作ロードマップ</h2>
      <p class="sec-p">4 つのレーンを同時に進めて、最後に合流して歩かせる。丸をタップすると作業ページが開きます。</p>
      <Roadmap />
    </section>

    <section class="lanes">
      <h2 class="sec-h"><span>👥</span> レーン別の状況</h2>
      <p class="sec-p">1 人 1 レーンが基本。手が空いたら「いますぐ始められる」作業を拾ってください。</p>
      <div class="lane-grid">
        <div v-for="l in lanes" :key="l.id" class="lane" :style="{ '--c': l.color }">
          <div class="lane-h"><span class="lic">{{ l.icon }}</span><div><b>レーン {{ l.id.toUpperCase() }} · {{ l.name }}</b><br /><small>{{ l.desc }}</small></div></div>
          <div class="owner">担当: <b>{{ l.owner || '未定' }}</b></div>
          <ul>
            <li v-for="s in laneSteps(l.id)" :key="s.id" :class="s.status">
              <a :href="'/steps/' + s.id"><span class="chip">{{ st[s.status] }}</span> <span class="sid">{{ s.id.toUpperCase() }}</span> {{ s.title }} <small>({{ s.minutes }} 分)</small></a>
            </li>
          </ul>
        </div>
      </div>
    </section>

    <section class="news">
      <h2 class="sec-h"><span>📰</span> 最新情報</h2>
      <ul>
        <li v-for="(n, i) in news" :key="i"><span class="date">{{ n.date }}</span>{{ n.text }}</li>
      </ul>
    </section>

    <section class="howto">
      <h2 class="sec-h"><span>📘</span> このサイトの使い方</h2>
      <div class="how-grid">
        <div><b>1. 自分のレーンを開く</b><p>ロードマップの丸、またはレーン別の一覧から作業ページへ。</p></div>
        <div><b>2. 図を見ながら進める</b><p>各手順は「図 4 : 文章 6」。太字の点線の付いた言葉は、触れる (タップする) と用語解説が出ます。</p></div>
        <div><b>3. チェックを付ける</b><p>ページ下の完了チェックは、その端末のブラウザに保存されます。</p></div>
        <div><b>4. 紙で使う</b><p>作業ページ右上の「印刷」ボタンで A4 に印刷できます。メニューは自動で消えます。</p></div>
        <div><b>5. 進捗を更新する</b><p>このトップの状態 (完了/作業中/イマココ) は管理者が更新します。終わったら管理者に一言。</p></div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.home { --ink: #1f2430; color: var(--ink); }
.hero { display: grid; grid-template-columns: 1.1fr 1fr; gap: 28px; align-items: center; padding: 48px 32px 24px; max-width: 1280px; margin: 0 auto; }
.kicker { font-family: var(--tk-display); letter-spacing: .12em; color: #1d6fd8; margin: 0 0 6px; font-size: 14px; }
.hero h1 { font-family: var(--tk-display); font-weight: 400; font-size: clamp(44px, 7vw, 82px); line-height: 1.05; margin: 0 0 16px; text-shadow: 4px 4px 0 #ffd23f; }
.lead { font-size: 18px; line-height: 1.8; margin: 0 0 20px; }
.cta { display: flex; gap: 10px; flex-wrap: wrap; }
.btn { display: inline-block; padding: 10px 20px; border: 3px solid var(--ink); border-radius: 12px; font-weight: 900; text-decoration: none; color: var(--ink); background: #fff; box-shadow: 4px 4px 0 var(--ink); transition: transform .1s; }
.btn:hover { transform: translate(2px, 2px); box-shadow: 2px 2px 0 var(--ink); }
.btn.primary { background: #ffd23f; }
.hero-art { position: relative; }
.hero-art img { width: 100%; border: 4px solid var(--ink); border-radius: 18px; box-shadow: 8px 8px 0 var(--ink); background: #fff; }
.tag { position: absolute; left: 12px; bottom: -14px; background: var(--ink); color: #fff; padding: 4px 12px; border-radius: 999px; font-size: 13px; font-family: var(--tk-hand); }
@media (max-width: 900px) { .hero { grid-template-columns: 1fr; padding: 28px 18px 12px; } }

.progress { padding: 20px 32px; max-width: 1280px; margin: 0 auto; }
.pbox { border: 4px solid var(--ink); border-radius: 18px; padding: 18px 22px; background: #fff; box-shadow: 8px 8px 0 #1d6fd8; }
.ptop { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
.plabel { font-family: var(--tk-display); font-size: 20px; }
.pnum { font-family: var(--tk-display); font-size: 48px; color: #1d6fd8; line-height: 1; }
.pnum small { font-size: 20px; }
.pcount { font-weight: 900; }
.pupd { margin-left: auto; color: #666; font-size: 13px; }
.bar { position: relative; height: 26px; border: 3px solid var(--ink); border-radius: 999px; background: repeating-linear-gradient(45deg, #f3f1e8 0 8px, #e9e6da 8px 16px); overflow: visible; margin: 12px 0 18px; }
.fill { height: 100%; background: linear-gradient(90deg, #27a86c, #7be29d); border-radius: 999px; transition: width .6s; }
.doing-mark { position: absolute; top: -12px; width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent; border-top: 14px solid #ff6a00; transform: translateX(-10px); }
.now-row { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 800px) { .now-row { grid-template-columns: 1fr; } }
.now-h { font-weight: 900; margin-bottom: 8px; font-family: var(--tk-hand); font-size: 17px; }
.now-card { display: flex; gap: 10px; align-items: flex-start; padding: 8px 12px; border: 2px solid var(--c); border-left-width: 10px; border-radius: 12px; margin-bottom: 8px; text-decoration: none; color: inherit; background: #fff; }
.now-card:hover { background: #f6f9ff; }
.now-card .ic { font-size: 26px; }
.now-card small { color: #556; }
.muted { color: #888; font-size: 14px; }

.sec-h { font-family: var(--tk-display); font-weight: 400; font-size: 32px; margin: 0 0 6px; }
.sec-h span { margin-right: 6px; }
.sec-p { margin: 0 0 16px; color: #445; }
.roadmap { padding: 28px 32px; max-width: 1280px; margin: 0 auto; }
.lanes { background: #f4f6fb; padding: 32px; }
.lanes > * { max-width: 1216px; margin-left: auto; margin-right: auto; }
.lane-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
.lane { background: #fff; border: 3px solid var(--c); border-radius: 16px; padding: 14px; box-shadow: 5px 5px 0 var(--c); }
.lane-h { display: flex; gap: 10px; align-items: flex-start; }
.lic { font-size: 30px; }
.lane small { color: #556; }
.owner { margin: 8px 0; font-size: 14px; background: color-mix(in srgb, var(--c) 12%, #fff); border-radius: 8px; padding: 2px 8px; display: inline-block; }
.lane ul { list-style: none; padding: 0; margin: 4px 0 0; }
.lane li a { display: block; padding: 6px 0; text-decoration: none; color: inherit; border-top: 1px dashed #dde; font-size: 15px; }
.lane li a:hover { color: #1d6fd8; }
.chip { display: inline-block; font-size: 11px; font-weight: 900; padding: 0 7px; border-radius: 999px; background: #eee; color: #555; min-width: 44px; text-align: center; }
li.doing .chip { background: #ffd23f; color: #5a3d00; }
li.done .chip { background: #27a86c; color: #fff; }
li.done a { color: #7a8; text-decoration: line-through; }
.sid { font-family: var(--tk-display); }

.news { padding: 28px 32px; max-width: 1280px; margin: 0 auto; }
.news ul { list-style: none; padding: 0; margin: 0; }
.news li { padding: 8px 0; border-bottom: 1px dashed #ccd; }
.date { font-family: var(--tk-display); color: #1d6fd8; margin-right: 12px; }
.howto { padding: 20px 32px 60px; max-width: 1280px; margin: 0 auto; }
.how-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.how-grid div { background: #fff; border: 2px solid #dde; border-radius: 12px; padding: 12px 14px; }
.how-grid p { margin: 4px 0 0; font-size: 14px; color: #445; }
</style>
