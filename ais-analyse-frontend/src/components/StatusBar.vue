<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

const footerTime = ref('')

function updateClock() {
  const now = new Date()
  footerTime.value =
    '2026-03-12 ' + now.toLocaleTimeString('zh-CN', { hour12: false })
}

let timer: ReturnType<typeof setInterval>
onMounted(() => {
  updateClock()
  timer = setInterval(updateClock, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <footer
    class="flex items-center justify-between px-4 h-7 border-t border-slate-700/50 flex-shrink-0 text-[10px] text-slate-500"
    style="background: #0d1320"
  >
    <div class="flex items-center gap-4">
      <span>© 2026 AIS Analysis System</span>
      <span class="text-slate-600">|</span>
      <span>数据库: <span class="text-emerald-500">已连接</span></span>
      <span class="text-slate-600">|</span>
      <span>MobilityDB <span class="text-slate-400">v1.1</span></span>
    </div>
    <div class="flex items-center gap-4">
      <span>船舶总数: <span class="text-slate-300 font-mono">1,523</span></span>
      <span class="text-slate-600">|</span>
      <span>轨迹点: <span class="text-slate-300 font-mono">2.4M</span></span>
      <span class="text-slate-600">|</span>
      <span>{{ footerTime }}</span>
    </div>
  </footer>
</template>
