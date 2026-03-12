<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useAppStore } from '@/stores/app'

const store = useAppStore()
const currentTime = ref('')

function updateClock() {
  currentTime.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

let timer: ReturnType<typeof setInterval>

onMounted(() => {
  updateClock()
  timer = setInterval(updateClock, 1000)
})

onUnmounted(() => clearInterval(timer))

function onGlobalSearch(e: Event) {
  store.searchKeyword = (e.target as HTMLInputElement).value
}
</script>

<template>
  <header
    class="flex items-center justify-between px-4 h-12 border-b border-slate-700/50 flex-shrink-0 z-50"
    style="background: #111827"
  >
    <!-- Left -->
    <div class="flex items-center gap-3">
      <div class="flex items-center gap-2">
        <div
          class="w-8 h-8 rounded-lg bg-gradient-to-br from-ocean-500 to-blue-600 flex items-center justify-center"
        >
          <svg
            width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
          >
            <path d="M2 20a2 2 0 0 0 2-2V8l4-6h8l4 6v10a2 2 0 0 0 2 2" />
            <path d="M12 4v8" />
            <path d="M2 12h20" />
            <path d="M2 20h20" />
          </svg>
        </div>
        <div>
          <span class="text-sm font-semibold text-white tracking-wide">AIS 轨迹分析系统</span>
          <span class="text-[10px] text-slate-500 ml-2 font-mono">v1.0</span>
        </div>
      </div>
      <div class="w-px h-6 bg-slate-700 mx-2"></div>
      <div class="flex items-center gap-1.5">
        <div class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></div>
        <span class="text-xs text-slate-400">系统在线</span>
      </div>
    </div>

    <!-- Center: Global Search -->
    <div class="flex-1 max-w-md mx-8">
      <div class="relative">
        <svg
          class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500"
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          type="text"
          placeholder="搜索船舶 MMSI / 船名..."
          class="w-full pl-9 py-1.5 text-sm rounded-md border border-slate-700 outline-none focus:border-ocean-500"
          style="background: #1a2332; color: #e2e8f0"
          :value="store.searchKeyword"
          @input="onGlobalSearch"
        />
      </div>
    </div>

    <!-- Right -->
    <div class="flex items-center gap-3">
      <span class="text-xs text-slate-500 font-mono">{{ currentTime }}</span>
      <button
        class="w-8 h-8 rounded-md border border-slate-700 flex items-center justify-center text-slate-400 hover:bg-navy-700 hover:text-white transition"
        title="设置"
        @click="store.showToast('设置面板开发中...', 'info')"
      >
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="3" />
          <path
            d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"
          />
        </svg>
      </button>
      <div
        class="w-7 h-7 rounded-full bg-gradient-to-br from-ocean-400 to-indigo-500 flex items-center justify-center text-xs font-bold text-white cursor-pointer"
        title="管理员"
      >
        A
      </div>
    </div>
  </header>
</template>
