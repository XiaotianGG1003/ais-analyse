<script setup lang="ts">
import { ref } from 'vue'
import { useAppStore } from '@/stores/app'
import { VESSEL_TYPES } from '@/types'
import { formatDateTimeLocal } from '@/utils/geo'

const store = useAppStore()

const distancePanelOpen = ref(false)
const activeQuick = ref('1h')

function setQuickTime(period: string) {
  activeQuick.value = period
  const now = new Date('2026-03-12T12:00:00')
  let ms = 0
  switch (period) {
    case '1h': ms = 3600000; break
    case '6h': ms = 6 * 3600000; break
    case '24h': ms = 24 * 3600000; break
    case '7d': ms = 7 * 24 * 3600000; break
  }
  const start = new Date(now.getTime() - ms)
  store.timeStart = formatDateTimeLocal(start)
  store.timeEnd = formatDateTimeLocal(now)
}

// Init default time range
store.timeStart = '2026-03-12T00:00'
store.timeEnd = '2026-03-12T23:59'

const emit = defineEmits<{
  queryTrack: []
  areaDetect: []
  predict: []
  calcDistance: [shipA: number, shipB: number]
}>()

function onQueryTrack() {
  if (!store.selectedShip) {
    store.showToast('请先选择船舶', 'warning')
    return
  }
  emit('queryTrack')
}

function onAreaDetect() {
  if (!store.selectedShip) {
    store.showToast('请先选择船舶', 'warning')
    return
  }
  store.areaDrawMode = !store.areaDrawMode
  emit('areaDetect')
}

function onPredict() {
  if (!store.selectedShip) {
    store.showToast('请先选择船舶', 'warning')
    return
  }
  emit('predict')
}

function onExport() {
  if (!store.selectedShip) {
    store.showToast('请先选择船舶后再导出', 'warning')
    return
  }
  store.showToast(store.selectedShip.vessel_name + ' 的数据已导出为 CSV', 'success')
}

function onCalcDist() {
  const a = store.distanceShipA
  const b = store.distanceShipB
  if (!a || !b) return
  if (a === b) {
    store.showToast('两艘船不能是同一艘', 'warning')
    return
  }
  emit('calcDistance', a, b)
}

const quickList = [
  { label: '1小时', value: '1h' },
  { label: '6小时', value: '6h' },
  { label: '24小时', value: '24h' },
  { label: '7天', value: '7d' },
]
</script>

<template>
  <aside
    v-show="store.leftPanelOpen"
    class="flex-shrink-0 overflow-y-auto border-r border-slate-700/50 flex flex-col transition-all duration-300"
    style="width: 320px; background: #111827"
  >
    <!-- Panel Header -->
    <div class="px-4 py-3 border-b border-slate-700/30 flex items-center justify-between">
      <span class="text-xs font-semibold text-slate-400 uppercase tracking-wider">查询与控制</span>
      <button
        class="w-6 h-6 rounded-md border border-slate-700 flex items-center justify-center text-slate-400 hover:bg-navy-700 hover:text-white transition"
        @click="store.toggleLeftPanel()"
      >
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>
    </div>

    <!-- Ship Search -->
    <div class="px-4 py-3 border-b border-slate-700/20">
      <label class="text-xs text-slate-500 font-medium mb-2 block">船舶查询</label>
      <div class="flex gap-2">
        <div class="relative flex-1">
          <svg
            class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            placeholder="MMSI / 船名"
            class="w-full pl-8 py-1.5 text-xs rounded-md border border-slate-700 outline-none focus:border-ocean-500"
            style="background: #1a2332; color: #e2e8f0"
            v-model="store.searchKeyword"
          />
        </div>
        <button
          class="px-3 py-1.5 text-xs font-medium text-white rounded-md"
          style="background: linear-gradient(135deg, #0ea5e9, #0284c7)"
        >
          搜索
        </button>
      </div>
    </div>

    <!-- Time Range -->
    <div class="px-4 py-3 border-b border-slate-700/20">
      <label class="text-xs text-slate-500 font-medium mb-2 block">时间范围</label>
      <div class="flex gap-1.5 mb-2 flex-wrap">
        <button
          v-for="q in quickList"
          :key="q.value"
          class="text-[11px] px-2.5 py-1 rounded border transition"
          :class="
            activeQuick === q.value
              ? 'bg-ocean-500/20 text-ocean-400 border-ocean-500/30'
              : 'bg-slate-700/50 text-slate-400 border-slate-600/30 hover:bg-slate-600/50'
          "
          @click="setQuickTime(q.value)"
        >
          {{ q.label }}
        </button>
      </div>
      <div class="space-y-2">
        <div>
          <span class="text-[10px] text-slate-500 mb-1 block">起始时间</span>
          <input
            type="datetime-local"
            v-model="store.timeStart"
            class="w-full text-xs py-1.5 rounded-md border border-slate-700 outline-none focus:border-ocean-500"
            style="background: #1a2332; color: #e2e8f0"
          />
        </div>
        <div>
          <span class="text-[10px] text-slate-500 mb-1 block">结束时间</span>
          <input
            type="datetime-local"
            v-model="store.timeEnd"
            class="w-full text-xs py-1.5 rounded-md border border-slate-700 outline-none focus:border-ocean-500"
            style="background: #1a2332; color: #e2e8f0"
          />
        </div>
      </div>
      <button
        class="w-full mt-2 py-1.5 text-xs font-medium text-white rounded-md"
        style="background: linear-gradient(135deg, #0ea5e9, #0284c7)"
        @click="onQueryTrack"
      >
        <span class="flex items-center justify-center gap-1.5">
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
          </svg>
          查询轨迹
        </span>
      </button>
    </div>

    <!-- Tools -->
    <div class="px-4 py-3 border-b border-slate-700/20">
      <label class="text-xs text-slate-500 font-medium mb-2 block">分析工具</label>
      <div class="grid grid-cols-2 gap-2">
        <button
          class="text-xs py-2 px-2 border rounded-md flex items-center justify-center gap-1.5 transition"
          :class="
            store.areaDrawMode
              ? 'bg-ocean-500/20 border-ocean-500/40 text-ocean-400'
              : 'bg-navy-600 border-slate-700 text-slate-300 hover:bg-navy-500'
          "
          @click="onAreaDetect"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <rect x="3" y="3" width="18" height="18" rx="2" />
          </svg>
          {{ store.areaDrawMode ? '取消绘制' : '区域检测' }}
        </button>
        <button
          class="text-xs py-2 px-2 bg-navy-600 border border-slate-700 text-slate-300 rounded-md flex items-center justify-center gap-1.5 hover:bg-navy-500 transition"
          @click="distancePanelOpen = !distancePanelOpen"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" />
          </svg>
          两船距离
        </button>
        <button
          class="text-xs py-2 px-2 bg-navy-600 border border-slate-700 text-slate-300 rounded-md flex items-center justify-center gap-1.5 hover:bg-navy-500 transition"
          @click="onPredict"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path d="M12 2L2 7l10 5 10-5-10-5z" />
            <path d="M2 17l10 5 10-5" />
            <path d="M2 12l10 5 10-5" />
          </svg>
          轨迹预测
        </button>
        <button
          class="text-xs py-2 px-2 bg-navy-600 border border-slate-700 text-slate-300 rounded-md flex items-center justify-center gap-1.5 hover:bg-navy-500 transition"
          @click="onExport"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          导出数据
        </button>
      </div>
    </div>

    <!-- Distance Calculator -->
    <div v-if="distancePanelOpen" class="px-4 py-3 border-b border-slate-700/20">
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-slate-500 font-medium">两船距离计算</label>
        <button class="text-slate-500 hover:text-slate-300" @click="distancePanelOpen = false">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div class="space-y-2">
        <div>
          <span class="text-[10px] text-slate-500 mb-1 block">船舶 A (MMSI)</span>
          <select
            v-model.number="store.distanceShipA"
            class="w-full text-xs py-1.5 rounded-md border border-slate-700 outline-none focus:border-ocean-500"
            style="background: #1a2332; color: #e2e8f0"
          >
            <option :value="null">选择船舶...</option>
            <option v-for="s in store.ships" :key="s.mmsi" :value="s.mmsi">
              {{ s.vessel_name }} ({{ s.mmsi }})
            </option>
          </select>
        </div>
        <div>
          <span class="text-[10px] text-slate-500 mb-1 block">船舶 B (MMSI)</span>
          <select
            v-model.number="store.distanceShipB"
            class="w-full text-xs py-1.5 rounded-md border border-slate-700 outline-none focus:border-ocean-500"
            style="background: #1a2332; color: #e2e8f0"
          >
            <option :value="null">选择船舶...</option>
            <option v-for="s in store.ships" :key="s.mmsi" :value="s.mmsi">
              {{ s.vessel_name }} ({{ s.mmsi }})
            </option>
          </select>
        </div>
        <button
          class="w-full py-1.5 text-xs font-medium text-white rounded-md"
          style="background: linear-gradient(135deg, #0ea5e9, #0284c7)"
          @click="onCalcDist"
        >
          计算距离
        </button>
      </div>
    </div>

    <!-- Ship List -->
    <div class="flex-1 overflow-y-auto px-2 py-2">
      <div class="flex items-center justify-between px-2 mb-2">
        <span class="text-[11px] text-slate-500 font-medium">船舶列表</span>
        <span class="text-[10px] text-slate-600 font-mono">{{ store.filteredShips.length }} 艘</span>
      </div>
      <div class="space-y-1">
        <div
          v-for="ship in store.filteredShips"
          :key="ship.mmsi"
          class="rounded-lg px-3 py-2.5 cursor-pointer flex items-center gap-3 transition-all duration-200 hover:bg-navy-600"
          :class="
            store.selectedMMSI === ship.mmsi
              ? 'bg-ocean-500/10 border border-ocean-500/20'
              : 'border border-transparent'
          "
          @click="store.selectShip(ship.mmsi)"
        >
          <div
            class="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
            :style="{ background: ship.color + '22' }"
          >
            <svg width="16" height="16" viewBox="0 0 28 28">
              <polygon points="14,4 20,22 14,19 8,22" :fill="ship.color" />
            </svg>
          </div>
          <div class="flex-1 min-w-0">
            <div class="text-xs font-medium text-slate-200 truncate">{{ ship.vessel_name }}</div>
            <div class="text-[10px] text-slate-500 font-mono">{{ ship.mmsi }}</div>
          </div>
          <div class="text-right flex-shrink-0">
            <div
              class="text-[11px] font-mono"
              :class="ship.position.sog > 0 ? 'text-ocean-400' : 'text-slate-500'"
            >
              {{ ship.position.sog }} kn
            </div>
            <div class="text-[10px] text-slate-500">
              {{ VESSEL_TYPES[ship.vessel_type] || '未知' }}
            </div>
          </div>
        </div>
      </div>
    </div>
  </aside>

  <!-- Collapsed toggle -->
  <button
    v-show="!store.leftPanelOpen"
    class="flex-shrink-0 w-6 flex items-center justify-center border-r border-slate-700/50 hover:bg-navy-700 transition cursor-pointer"
    style="background: #111827"
    @click="store.toggleLeftPanel()"
  >
    <svg width="14" height="14" fill="none" stroke="#94a3b8" viewBox="0 0 24 24">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  </button>
</template>
