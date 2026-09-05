<script setup>
import { computed } from 'vue'
import { glossary } from '../../data/glossary.js'
const props = defineProps({ k: String })
const g = computed(() => glossary[props.k])
</script>
<template>
  <span class="tk-term" :title="g ? g.desc : ''" tabindex="0"><slot>{{ g ? g.term.split(' (')[0] : k }}</slot><span class="tk-term-pop" v-if="g"><b>{{ g.term }}</b><br />{{ g.desc }}</span></span>
</template>
<style scoped>
.tk-term { position: relative; border-bottom: 2px dotted var(--vp-c-brand-1); cursor: help; font-weight: 700; color: #123; }
.tk-term-pop { display: none; position: absolute; left: 0; bottom: 130%; z-index: 30; width: 300px; background: #1f2430; color: #fff; font-weight: 400; font-size: 13px; line-height: 1.55; padding: 10px 12px; border-radius: 10px; box-shadow: 0 6px 20px rgba(0,0,0,.25); }
.tk-term:hover .tk-term-pop, .tk-term:focus .tk-term-pop { display: block; }
@media print { .tk-term { border-bottom: 1px dotted #444; } .tk-term-pop { display: none !important; } }
</style>
