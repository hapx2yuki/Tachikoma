<script setup>
import { computed } from 'vue'
import { steps, lanes } from '../../data/steps.js'
const props = defineProps({ step: String })
const me = computed(() => steps.find(s => s.id === props.step))
const next = computed(() => steps.filter(s => s.deps.includes(props.step)))
const laneOf = id => lanes.find(l => l.id === id)
</script>
<template>
  <section class="tk-next" v-if="me">
    <h2 id="next">➡️ この作業が終わったら</h2>
    <p v-if="!next.length">この作業の後に直接続く作業はありません。ダッシュボードで全体の状況を確認してください。</p>
    <div class="cards" v-else>
      <a v-for="s in next" :key="s.id" :href="'/steps/' + s.id" :style="{ '--c': laneOf(s.lane).color }">
        <span class="ic">{{ s.icon }}</span>
        <span><b>{{ s.id.toUpperCase() }} {{ s.title }}</b><br /><small>{{ s.summary }}</small><br /><small class="dep" v-if="s.deps.length > 1">前提: {{ s.deps.map(d => d.toUpperCase()).join(' + ') }}</small></span>
      </a>
    </div>
    <p class="back"><a href="/">⬅ ダッシュボードへ戻る</a></p>
  </section>
</template>
<style scoped>
.tk-next { margin-top: 36px; }
.tk-next h2 { border: none; }
.tk-next h2::before { display: none; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.cards a { display: flex; gap: 12px; align-items: flex-start; text-decoration: none; color: inherit; border: 2px solid var(--c); border-left-width: 10px; border-radius: 12px; padding: 10px 12px; background: #fff; }
.cards a:hover { background: #f8fbff; }
.ic { font-size: 28px; }
small { color: #556; }
.dep { color: #a33; }
.back { margin-top: 14px; }
@media print { .tk-next { display: none; } }
</style>
