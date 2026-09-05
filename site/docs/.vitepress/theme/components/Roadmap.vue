<script setup>
import { computed } from 'vue'
import { steps, lanes } from '../../data/steps.js'

const rowY = { a: 130, b: 265, c: 400, d: 535 }
const startNode = { id: 'start', x: 110, y: 332, icon: '🖨️', title: '3Dプリント\n全パーツ完了', status: 'done', big: true }
const goalNode = { id: 'goal', x: 1190, y: 560 }
const ePos = { e1: [930, 332], e2: [1040, 200], e3: [1150, 332] }

const nodes = computed(() => {
  const list = [startNode]
  for (const l of ['a', 'b', 'c', 'd']) {
    steps.filter(s => s.lane === l).forEach((s, i) => list.push({ ...s, x: 330 + i * 210, y: rowY[l] }))
  }
  for (const s of steps.filter(s => s.lane === 'e')) list.push({ ...s, x: ePos[s.id][0], y: ePos[s.id][1] })
  return list
})
const byId = computed(() => Object.fromEntries([...nodes.value, goalNode].map(n => [n.id, n])))
const laneColor = id => (lanes.find(l => l.id === id) || {}).color || '#333'

// 接続線 (依存関係 + レーン内の順序)
const edges = computed(() => {
  const e = []
  for (const l of ['a', 'b', 'c', 'd']) {
    const ss = steps.filter(s => s.lane === l)
    e.push({ from: 'start', to: ss[0].id, lane: l })
    for (let i = 1; i < ss.length; i++) e.push({ from: ss[i - 1].id, to: ss[i].id, lane: l })
    e.push({ from: ss[ss.length - 1].id, to: 'e1', lane: l })
  }
  e.push({ from: 'e1', to: 'e2', lane: 'e' }, { from: 'e2', to: 'e3', lane: 'e' }, { from: 'e3', to: 'goal', lane: 'e' })
  return e.map(x => ({ ...x, d: curve(byId.value[x.from], byId.value[x.to]), done: byId.value[x.from].status === 'done' }))
})
function curve(a, b) {
  const dx = (b.x - a.x) * 0.5
  return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`
}
const nowNodes = computed(() => nodes.value.filter(n => n.now))
const doneCount = computed(() => steps.filter(s => s.status === 'done').length)
const burst = (cx, cy, r1, r2, n = 16) => {
  const pts = []
  for (let i = 0; i < n * 2; i++) { const r = i % 2 ? r2 : r1; const a = (Math.PI * i) / n; pts.push(`${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`) }
  return pts.join(' ')
}
</script>

<template>
  <div class="rm-wrap">
    <svg viewBox="0 0 1280 700" class="rm" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="製作ロードマップ">
      <defs>
        <filter id="wob" x="-5%" y="-5%" width="110%" height="110%"><feTurbulence type="fractalNoise" baseFrequency="0.015" numOctaves="2" seed="7" result="n"/><feDisplacementMap in="SourceGraphic" in2="n" scale="3"/></filter>
        <pattern id="dots" width="14" height="14" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.3" fill="#d9d2b8"/></pattern>
        <marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#1f2430"/></marker>
        <marker id="arrDone" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#1f9d55"/></marker>
        <radialGradient id="goalG"><stop offset="0" stop-color="#fff7b0"/><stop offset="1" stop-color="#ffd23f"/></radialGradient>
      </defs>
      <rect width="1280" height="700" fill="#fffdf4"/>
      <rect width="1280" height="700" fill="url(#dots)" opacity=".7"/>

      <!-- レーンの帯 -->
      <g v-for="l in lanes.filter(l => l.id !== 'e')" :key="l.id">
        <rect x="150" :y="rowY[l.id] - 52" width="680" height="104" rx="52" :fill="l.color" opacity=".07"/>
        <g :transform="`translate(195, ${rowY[l.id]})`">
          <rect x="-40" y="-32" width="106" height="64" rx="14" :fill="l.color" filter="url(#wob)"/>
          <text x="13" y="-6" text-anchor="middle" class="laneT">{{ l.icon }} <tspan class="laneN">{{ l.id.toUpperCase() }}</tspan></text>
          <text x="13" y="16" text-anchor="middle" class="laneNm">{{ l.name }}</text>
          <text v-if="l.owner" x="13" y="46" text-anchor="middle" class="owner" :fill="l.color">{{ l.owner }}</text>
        </g>
      </g>

      <!-- 接続線 -->
      <g fill="none" filter="url(#wob)">
        <path v-for="(e, i) in edges" :key="'w' + i" :d="e.d" stroke="#fffdf4" stroke-width="12" stroke-linecap="round"/>
        <path v-for="(e, i) in edges" :key="i" :d="e.d" :stroke="e.done ? '#1f9d55' : '#1f2430'" stroke-width="5" stroke-linecap="round" :stroke-dasharray="e.done ? '' : '14 10'" :marker-end="e.done ? 'url(#arrDone)' : 'url(#arr)'"/>
      </g>

      <!-- ゴール -->
      <g :transform="`translate(${goalNode.x}, ${goalNode.y})`">
        <polygon :points="burst(0, 0, 82, 66)" fill="#1f2430" transform="rotate(4)"/>
        <polygon :points="burst(0, 0, 76, 60)" fill="url(#goalG)" transform="rotate(4)"/>
        <text y="-8" text-anchor="middle" class="goalT">タチコマ</text>
        <text y="26" text-anchor="middle" class="goalT">歩行!!</text>
      </g>

      <!-- ノード -->
      <g v-for="n in nodes" :key="n.id" :transform="`translate(${n.x}, ${n.y})`" :class="['node', n.status]">
        <a :href="n.id === 'start' ? '/guide/start' : '/steps/' + n.id">
          <circle v-if="n.now" :r="(n.big ? 52 : 40) + 8" fill="none" stroke="#ff6a00" stroke-width="4" class="pulse"/>
          <circle :r="n.big ? 52 : 40" fill="#1f2430" transform="translate(3,4)"/>
          <circle :r="n.big ? 52 : 40" :fill="n.status === 'done' ? '#d5f5df' : n.status === 'doing' ? '#fff2b8' : '#fff'" :stroke="n.status === 'todo' ? '#8a8f9c' : '#1f2430'" stroke-width="4" :stroke-dasharray="n.status === 'todo' ? '8 6' : ''" filter="url(#wob)"/>
          <text y="12" text-anchor="middle" :class="['ic', { big: n.big }]" :opacity="n.status === 'todo' ? .55 : 1">{{ n.icon }}</text>
          <text v-if="n.id !== 'start'" y="-50" text-anchor="middle" class="lbl">{{ n.id.toUpperCase() }}</text>
          <text :y="(n.big ? 52 : 40) + 22" text-anchor="middle" class="ttl" :fill="n.status === 'todo' ? '#5a6070' : '#1f2430'">
            <tspan v-for="(ln, i) in (n.title || '').split('\n')" :key="i" x="0" :dy="i ? 18 : 0">{{ ln }}</tspan>
          </text>
          <g v-if="n.status === 'done'" transform="rotate(-18) translate(26,-30)">
            <circle r="20" fill="none" stroke="#d3232a" stroke-width="3"/>
            <text y="7" text-anchor="middle" class="stamp">済</text>
          </g>
          <g v-if="n.now" :transform="`translate(0, ${-(n.big ? 52 : 40) - 58})`" class="now">
            <path d="M-56,-22 h112 a10,10 0 0 1 10,10 v22 a10,10 0 0 1 -10,10 h-40 l-8,12 l-6,-12 h-58 a10,10 0 0 1 -10,-10 v-22 a10,10 0 0 1 10,-10 z" fill="#ff6a00" stroke="#1f2430" stroke-width="3"/>
            <text y="5" text-anchor="middle" class="nowT">イマココ!</text>
          </g>
        </a>
      </g>

      <text x="1120" y="40" text-anchor="end" class="hand">✔ {{ doneCount }} / {{ steps.length }} 作業クリア</text>
      <text x="1120" y="64" text-anchor="end" class="hand small">点線 = これから · 緑の実線 = 通過済み · 丸をタップで作業ページへ</text>
    </svg>
  </div>
</template>

<style scoped>
.rm-wrap { width: 100%; overflow-x: auto; border: 4px solid #1f2430; border-radius: 18px; background: #fffdf4; box-shadow: 8px 8px 0 #1f2430; }
.rm { display: block; min-width: 900px; width: 100%; height: auto; font-family: var(--tk-hand); }
.laneT { font-size: 20px; }
.laneN { font-size: 18px; fill: #fff; font-family: var(--tk-display); }
.laneNm { font-size: 11px; fill: #fff; font-weight: 900; }
.laneName { font-size: 14px; font-weight: 900; font-family: var(--tk-hand); }
.owner { font-size: 14px; }
.ic { font-size: 36px; }
.ic.big { font-size: 46px; }
.lbl { font-size: 15px; font-family: var(--tk-display); fill: #1f2430; }
.ttl { font-size: 15px; font-weight: 700; }
.stamp { font-size: 22px; fill: #d3232a; font-family: var(--tk-display); }
.goalT { font-size: 30px; font-family: var(--tk-display); fill: #1f2430; }
.nowT { font-size: 20px; fill: #fff; font-family: var(--tk-display); }
.hand { font-size: 18px; fill: #1f2430; }
.hand.small { font-size: 13px; fill: #555; }
.node a { cursor: pointer; }
.node a:hover .ttl { text-decoration: underline; }
.pulse { animation: pulse 1.4s ease-in-out infinite; transform-origin: center; }
@keyframes pulse { 0% { opacity: .2; r: 44; } 50% { opacity: 1; } 100% { opacity: .2; r: 56; } }
.now { animation: bob 1.2s ease-in-out infinite; }
@keyframes bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
@media print { .rm-wrap { box-shadow: none; } .pulse, .now { animation: none; } }
</style>
