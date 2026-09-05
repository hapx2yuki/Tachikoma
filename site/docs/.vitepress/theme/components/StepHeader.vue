<script setup>
import { computed } from 'vue'
import { steps, lanes } from '../../data/steps.js'
const props = defineProps({ step: String, tools: { type: Array, default: () => [] }, parts: { type: Array, default: () => [] }, level: { type: Number, default: 2 }, goal: { type: String, default: '' } })
const me = computed(() => steps.find(s => s.id === props.step) || {})
const lane = computed(() => lanes.find(l => l.id === me.value.lane) || {})
const deps = computed(() => me.value.deps ? me.value.deps.map(d => steps.find(s => s.id === d)).filter(Boolean) : [])
const st = { todo: '未着手', doing: '作業中', done: '完了', blocked: '停止中' }
const print = () => window.print()
</script>
<template>
  <header class="tk-head" :style="{ '--c': lane.color }">
    <div class="top">
      <span class="lane">{{ lane.icon }} レーン {{ (me.lane || '').toUpperCase() }} · {{ lane.name }}</span>
      <span class="status" :class="me.status">{{ st[me.status] || '' }}</span>
      <button class="tk-noprint print" @click="print">🖨 このページを印刷</button>
    </div>
    <h1><span class="id">{{ (me.id || '').toUpperCase() }}</span> {{ me.title }}</h1>
    <p class="goal" v-if="goal">🎯 <b>ゴール:</b> {{ goal }}</p>
    <div class="meta">
      <div><b>⏱ 目安</b><span>約 {{ me.minutes }} 分</span></div>
      <div><b>📶 難しさ</b><span>{{ '★'.repeat(level) }}{{ '☆'.repeat(4 - level) }}</span></div>
      <div><b>🔗 前提の作業</b><span v-if="!deps.length">なし (すぐ始められる)</span><span v-else><a v-for="d in deps" :key="d.id" :href="'/steps/' + d.id">{{ d.id.toUpperCase() }} {{ d.title }}</a></span></div>
    </div>
    <div class="lists">
      <div v-if="tools.length"><b>🧰 使う工具</b><ul><li v-for="t in tools" :key="t">{{ t }}</li></ul></div>
      <div v-if="parts.length"><b>📦 使う部品</b><ul><li v-for="p in parts" :key="p" v-html="p"></li></ul></div>
    </div>
  </header>
</template>
<style scoped>
.tk-head { border: 3px solid var(--c); border-radius: 20px; padding: 16px 22px 14px; margin: 8px 0 28px; background: linear-gradient(135deg, #fff 0%, #fff 60%, color-mix(in srgb, var(--c) 12%, #fff) 100%); break-inside: avoid; }
.top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.lane { background: var(--c); color: #fff; font-weight: 900; padding: 2px 12px; border-radius: 999px; font-size: .9em; }
.status { padding: 2px 10px; border-radius: 999px; font-weight: 900; font-size: .85em; background: #eee; }
.status.doing { background: #fff1c2; color: #7a5a00; }
.status.done { background: #d9f5e2; color: #146b34; }
.status.blocked { background: #ffd9d9; color: #8a1a1a; }
.print { margin-left: auto; border: 2px solid #333; background: #fff; border-radius: 10px; padding: 4px 12px; font-weight: 700; cursor: pointer; }
.print:hover { background: #333; color: #fff; }
h1 { margin: 10px 0 6px; font-size: 2em; line-height: 1.25; }
.id { display: inline-block; background: var(--c); color: #fff; padding: 0 12px; border-radius: 10px; font-family: var(--tk-display); margin-right: 8px; }
.goal { margin: 4px 0 10px; font-size: 1.05em; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
.meta div, .lists > div { background: rgba(255,255,255,.75); border-radius: 10px; padding: 6px 10px; }
.meta b, .lists b { display: block; font-size: .8em; color: #555; }
.meta a { margin-right: 10px; font-weight: 700; }
.lists { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
.lists ul { margin: 2px 0 0; padding-left: 1.2em; font-size: .93em; line-height: 1.5; }
@media (max-width: 700px) { .lists { grid-template-columns: 1fr; } }
@media print { .tk-head { padding: 10px 14px; margin-bottom: 14px; } h1 { font-size: 1.6em; } }
</style>
