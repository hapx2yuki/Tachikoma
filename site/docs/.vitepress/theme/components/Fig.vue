<script setup>
defineProps({ src: String, alt: { type: String, default: '' }, cap: { type: String, default: '' }, w: { type: String, default: '100%' } })
</script>
<template>
  <figure class="tk-fig" :style="{ maxWidth: w }">
    <img :src="src" :alt="alt || cap" loading="lazy" />
    <figcaption v-if="cap">{{ cap }}</figcaption>
  </figure>
</template>
<style scoped>
.tk-fig { margin: 0; }
.tk-fig img { width: 100%; height: auto; display: block; border-radius: 10px; background: #fff; border: 1px solid #e6e8ee; }
figcaption { font-size: .82em; color: #556; margin-top: 6px; line-height: 1.5; font-family: var(--tk-hand); }
</style>
