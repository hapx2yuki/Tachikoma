<script setup>
defineProps({ type: { type: String, default: 'info' }, title: { type: String, default: '' } })
const icons = { info: '💡', warn: '⚠️', danger: '🚫', ok: '✅', tool: '🧰', why: '🤔' }
</script>
<template>
  <div class="tk-panel" :class="type">
    <div class="ttl"><span>{{ icons[type] || '💡' }}</span> {{ title }}</div>
    <div class="body"><slot /></div>
  </div>
</template>
<style scoped>
.tk-panel { border-radius: 12px; padding: 10px 14px; margin: 12px 0; border: 2px solid; break-inside: avoid; font-size: .95em; }
.tk-panel .ttl { font-weight: 900; margin-bottom: 4px; }
.tk-panel .body :deep(p) { margin: 4px 0; }
.tk-panel .body :deep(ul) { margin: 4px 0; padding-left: 1.2em; }
.info { background: #eef4ff; border-color: #8fb4ff; }
.warn { background: #fff6dc; border-color: #f2b233; }
.danger { background: #ffe7e7; border-color: #e5484d; }
.ok { background: #eaf8ee; border-color: #5fc37f; }
.tool { background: #f3f0ff; border-color: #b39ddb; }
.why { background: #fdf2f8; border-color: #f0a6cf; }
</style>
