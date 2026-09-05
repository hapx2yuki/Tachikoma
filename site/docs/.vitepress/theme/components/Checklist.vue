<script setup>
import { onMounted } from 'vue'
import { store, isChecked, toggle, resetStep, reload } from '../progress.js'
const props = defineProps({ step: String, items: { type: Array, default: () => [] }, title: { type: String, default: '✅ 完了チェック (この端末に保存されます)' } })
onMounted(reload)
</script>
<template>
  <section class="tk-check">
    <h2 :id="'check-' + step">{{ title }}</h2>
    <ul>
      <li v-for="(it, i) in items" :key="i" :class="{ on: isChecked(step, i) }" @click="toggle(step, i)">
        <span class="box">{{ isChecked(step, i) ? '✔' : '' }}</span><span v-html="it"></span>
      </li>
    </ul>
    <p class="foot tk-noprint"><span>{{ items.filter((_, i) => isChecked(step, i)).length }} / {{ items.length }} 完了</span> <button @click="resetStep(step)">リセット</button></p>
  </section>
</template>
<style scoped>
.tk-check { margin-top: 36px; background: #eef8f1; border: 2px solid #9fd6b3; border-radius: 16px; padding: 10px 22px 12px; break-inside: avoid; }
.tk-check h2 { margin-top: 8px; border: none; font-size: 1.3em; }
.tk-check h2::before { display: none; }
ul { list-style: none; padding: 0; margin: 6px 0; }
li { display: flex; gap: 12px; align-items: flex-start; padding: 8px 10px; border-radius: 10px; cursor: pointer; line-height: 1.5; }
li:hover { background: #dff1e5; }
li.on { color: #4b6b57; text-decoration: line-through; }
.box { flex: none; width: 24px; height: 24px; border: 2px solid #2f8f5a; border-radius: 6px; background: #fff; display: inline-flex; align-items: center; justify-content: center; font-weight: 900; color: #2f8f5a; margin-top: 2px; }
.foot { display: flex; justify-content: space-between; align-items: center; font-size: .9em; color: #375; margin: 4px 0 0; }
button { border: 1px solid #9fd6b3; background: #fff; border-radius: 8px; padding: 2px 10px; font-size: .85em; cursor: pointer; }
</style>
