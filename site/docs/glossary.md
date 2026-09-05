---
title: 用語集
---
<script setup>
import { glossary } from './.vitepress/data/glossary.js'
const all = Object.entries(glossary)
</script>

# 📖 用語集 (電子工作が初めての人向け)

作業ページに出てくる言葉を全部まとめました。太字の点線が付いた言葉は、作業ページ上でも触れると説明が出ます。

<div class="tk-gl">
  <div v-for="[k, g] in all" :key="k" class="row"><b>{{ g.term }}</b><p>{{ g.desc }}</p></div>
</div>

<style scoped>
.tk-gl { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 12px; }
.row { background: #fbf8ef; border: 2px dashed #d9c98a; border-radius: 12px; padding: 10px 14px; }
.row b { color: #5a4a12; }
.row p { margin: 4px 0 0; font-size: .93em; line-height: 1.6; }
@media print { .tk-gl { grid-template-columns: 1fr 1fr; } }
</style>
