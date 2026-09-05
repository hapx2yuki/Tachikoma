<script setup>
import { computed } from 'vue'
import { glossary } from '../../data/glossary.js'
const props = defineProps({ terms: { type: Array, default: () => [] } })
const list = computed(() => props.terms.map(k => ({ k, ...(glossary[k] || { term: k, desc: '' }) })))
</script>
<template>
  <section class="tk-gloss">
    <h2 id="yougo">📖 このページの用語解説</h2>
    <dl>
      <div v-for="g in list" :key="g.k"><dt>{{ g.term }}</dt><dd>{{ g.desc }}</dd></div>
    </dl>
    <p class="more">もっと詳しく: <a href="/glossary">用語集 (全項目)</a></p>
  </section>
</template>
<style scoped>
.tk-gloss { margin-top: 44px; background: #fbf8ef; border: 2px dashed #d9c98a; border-radius: 16px; padding: 12px 22px 16px; break-inside: avoid; }
.tk-gloss h2 { margin-top: 8px; border: none; font-size: 1.3em; }
.tk-gloss h2::before { display: none; }
dl { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px 22px; margin: 0; }
dt { font-weight: 900; color: #5a4a12; }
dd { margin: 0; font-size: .93em; line-height: 1.6; color: #333; }
.more { font-size: .85em; margin: 12px 0 0; }
@media print { .more { display: none; } dl { grid-template-columns: 1fr 1fr; } }
</style>
