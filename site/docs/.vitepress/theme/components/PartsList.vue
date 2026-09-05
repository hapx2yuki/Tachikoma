<script setup>
import { ref, computed, onMounted } from 'vue'
import { parts, cats, materials } from '../../data/parts.js'
import { steps, lanes } from '../../data/steps.js'
const q = ref(''); const cat = ref('all'); const mat = ref('all'); const step = ref('all'); const view = ref('grid')
const catOf = id => cats.find(c => c.id === id) || {}
const stepOf = id => steps.find(s => s.id === id) || {}
const laneColor = sid => (lanes.find(l => l.id === (stepOf(sid).lane)) || {}).color || '#888'
const list = computed(() => parts.filter(p =>
  (cat.value === 'all' || p.cat === cat.value) &&
  (mat.value === 'all' || p.mat === mat.value) &&
  (step.value === 'all' || p.steps.includes(step.value)) &&
  (!q.value || (p.name + ' ' + p.desc + ' ' + (p.note || '') + ' ' + (p.bom || '')).toLowerCase().includes(q.value.toLowerCase()))
))
const totalQty = computed(() => list.value.reduce((a, p) => a + p.qty, 0))
const counts = computed(() => Object.fromEntries(cats.map(c => [c.id, parts.filter(p => p.cat === c.id).length])))
const reset = () => { q.value = ''; cat.value = 'all'; mat.value = 'all'; step.value = 'all' }
onMounted(() => { try { const h = new URLSearchParams(location.search); if (h.get('step')) step.value = h.get('step'); if (h.get('cat')) cat.value = h.get('cat') } catch {} })
</script>

<template>
  <div class="pl">
    <div class="filters tk-noprint">
      <input v-model="q" class="q" type="search" placeholder="🔍 名前・説明で検索 (例: ホーン, 0x41, M3)" />
      <div class="chips">
        <button :class="{ on: cat === 'all' }" @click="cat = 'all'">すべて <small>{{ parts.length }}</small></button>
        <button v-for="c in cats" :key="c.id" :class="{ on: cat === c.id }" :style="{ '--c': c.color }" @click="cat = c.id">{{ c.icon }} {{ c.name }} <small>{{ counts[c.id] }}</small></button>
      </div>
      <div class="row">
        <label>使う作業 <select v-model="step"><option value="all">すべて</option><option v-for="s in steps" :key="s.id" :value="s.id">{{ s.id.toUpperCase() }} {{ s.title }}</option></select></label>
        <label>材料 <select v-model="mat"><option value="all">すべて</option><option v-for="m in materials" :key="m" :value="m">{{ m }}</option></select></label>
        <label>表示 <select v-model="view"><option value="grid">カード</option><option value="table">表</option></select></label>
        <button class="reset" @click="reset">条件をクリア</button>
      </div>
      <p class="count">{{ list.length }} 種類 · 合計 {{ totalQty }} 個</p>
    </div>
    <p v-if="cat !== 'all'" class="catdesc">{{ catOf(cat).icon }} <b>{{ catOf(cat).name }}</b>: {{ catOf(cat).desc }}</p>

    <div v-if="view === 'grid'" class="grid">
      <div v-for="p in list" :key="p.id" class="card" :style="{ '--c': catOf(p.cat).color }">
        <div class="imgw"><img :src="p.img" :alt="p.name" loading="lazy" /><span class="qty">×{{ p.qty }}</span></div>
        <div class="body">
          <div class="name">{{ p.name }}</div>
          <div class="tags"><span class="tag cat">{{ catOf(p.cat).icon }} {{ catOf(p.cat).name }}</span><span v-if="p.mat" class="tag">{{ p.mat }}</span><span v-if="p.note" class="tag note">{{ p.note }}</span><span v-if="p.bom" class="tag">BOM {{ p.bom }}</span></div>
          <p class="desc">{{ p.desc }}</p>
          <div class="steps" v-if="p.steps.length"><a v-for="s in p.steps" :key="s" :href="'/steps/' + s" :style="{ '--l': laneColor(s) }">{{ s.toUpperCase() }} {{ stepOf(s).title }}</a></div>
          <div class="steps" v-else><span class="none">今回の組立では未使用 / 予備</span></div>
        </div>
      </div>
    </div>

    <table v-else class="tbl">
      <thead><tr><th>画像</th><th>名前</th><th>数</th><th>材料</th><th>説明</th><th>使う作業</th></tr></thead>
      <tbody>
        <tr v-for="p in list" :key="p.id">
          <td><img :src="p.img" :alt="p.name" loading="lazy" /></td>
          <td><b>{{ p.name }}</b><br /><small v-if="p.note">{{ p.note }}</small></td>
          <td class="c">{{ p.qty }}</td><td>{{ p.mat || '—' }}</td><td class="d">{{ p.desc }}</td>
          <td class="s"><a v-for="s in p.steps" :key="s" :href="'/steps/' + s">{{ s.toUpperCase() }}</a></td>
        </tr>
      </tbody>
    </table>
    <p v-if="!list.length" class="empty">該当するパーツがありません。条件をゆるめてください。</p>
  </div>
</template>

<style scoped>
.filters { background: #f4f6fb; border: 2px solid #dde3ee; border-radius: 16px; padding: 14px 16px 6px; margin: 8px 0 14px; }
.q { width: 100%; font-size: 16px; padding: 10px 14px; border: 2px solid #1f2430; border-radius: 12px; background: #fff; box-sizing: border-box; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.chips button { border: 2px solid var(--c, #1f2430); background: #fff; color: #1f2430; border-radius: 999px; padding: 4px 12px; font-weight: 700; cursor: pointer; font-size: 14px; }
.chips button small { opacity: .6; margin-left: 2px; }
.chips button.on { background: var(--c, #1f2430); color: #fff; }
.row { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; font-size: 14px; }
.row select { margin-left: 4px; padding: 4px 8px; border-radius: 8px; border: 1px solid #bbc; background: #fff; font-size: 14px; max-width: 260px; }
.reset { border: 1px solid #99a; background: #fff; border-radius: 8px; padding: 4px 10px; cursor: pointer; font-size: 13px; }
.count { margin: 8px 0 4px; font-weight: 700; color: #345; }
.catdesc { background: #fffbe6; border: 1px solid #f2b233; border-radius: 10px; padding: 6px 12px; font-size: 14px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 14px; }
.card { border: 2px solid #e3e6ee; border-top: 6px solid var(--c); border-radius: 14px; background: #fff; overflow: hidden; display: flex; flex-direction: column; break-inside: avoid; }
.imgw { position: relative; background: #fff; border-bottom: 1px solid #eee; }
.imgw img { width: 100%; aspect-ratio: 6 / 5; object-fit: contain; display: block; }
.qty { position: absolute; right: 8px; top: 8px; background: #1f2430; color: #fff; font-family: var(--tk-display); padding: 2px 10px; border-radius: 999px; font-size: 15px; }
.body { padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 6px; }
.name { font-weight: 900; line-height: 1.3; word-break: break-all; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.tag { font-size: 11px; background: #eef1f6; border-radius: 6px; padding: 1px 6px; color: #345; }
.tag.cat { background: color-mix(in srgb, var(--c) 18%, #fff); }
.tag.note { background: #ffe9d6; color: #7a3a00; font-weight: 700; }
.desc { margin: 0; font-size: 13.5px; line-height: 1.55; color: #223; flex: 1; }
.steps { display: flex; flex-wrap: wrap; gap: 4px; }
.steps a { font-size: 12px; text-decoration: none; color: #fff; background: var(--l); padding: 1px 8px; border-radius: 999px; font-weight: 700; }
.none { font-size: 12px; color: #888; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { border: 1px solid #dde; padding: 6px 8px; vertical-align: top; }
.tbl img { width: 80px; height: 66px; object-fit: contain; }
.tbl .c { text-align: center; font-weight: 900; }
.tbl .d { min-width: 240px; }
.tbl .s a { margin-right: 6px; font-weight: 700; }
.empty { text-align: center; color: #888; padding: 30px; }
@media print { .grid { grid-template-columns: repeat(3, 1fr); gap: 8px; } .card { border-color: #999; } .desc { font-size: 11px; } .steps a { color: #000 !important; background: none !important; border: 1px solid #333; } }
</style>
