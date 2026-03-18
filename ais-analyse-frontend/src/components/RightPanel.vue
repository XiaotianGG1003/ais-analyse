<script setup lang="ts">
import { ref, watch, nextTick, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { useAppStore } from '@/stores/app'
import { VESSEL_TYPES, NAV_STATUS } from '@/types'

const store = useAppStore()

const speedChartEl = ref<HTMLDivElement>()
const sogDistChartEl = ref<HTMLDivElement>()
let speedChart: echarts.ECharts | null = null
let sogDistChart: echarts.ECharts | null = null

function formatPositionTime(time?: string | null) {
  if (!time) return '-'
  const d = new Date(time)
  if (Number.isNaN(d.getTime())) return time
  return d.toLocaleString('zh-CN', { hour12: false })
}

function handleResize() {
  speedChart?.resize()
  sogDistChart?.resize()
}

watch(
  () => store.activeRightTab,
  async (tab) => {
    if (tab === 'stats' && store.trackStatistics && store.selectedShip) {
      await nextTick()
      renderSpeedChart()
      renderSOGDistChart()
    }
  },
)

function renderSpeedChart() {
  const ship = store.selectedShip
  const stats = store.trackStatistics
  if (!ship || !speedChartEl.value || !stats) return

  if (speedChart) speedChart.dispose()
  speedChart = echarts.init(speedChartEl.value)

  const times: string[] = []
  const speeds: number[] = []

  if (stats.speedSeries && stats.speedSeries.length > 0) {
    stats.speedSeries.forEach((pt) => {
      const d = new Date(pt.time)
      times.push(d.getHours() + ':' + String(d.getMinutes()).padStart(2, '0'))
      speeds.push(pt.speed)
    })
  } else {
    // Fallback: generate from track length
    const baseTime = new Date('2026-03-12T06:00:00')
    for (let i = 0; i < (ship.track.length || 6); i++) {
      const t = new Date(baseTime.getTime() + i * 30 * 60000)
      times.push(t.getHours() + ':' + String(t.getMinutes()).padStart(2, '0'))
      speeds.push(+(ship.position.sog * (0.5 + Math.random())).toFixed(1))
    }
  }

  speedChart.setOption({
    grid: { top: 10, right: 10, bottom: 24, left: 36 },
    xAxis: {
      type: 'category',
      data: times,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisLabel: { color: '#64748b', fontSize: 10 },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [
      {
        data: speeds,
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: ship.color, width: 2 },
        itemStyle: { color: ship.color },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: ship.color + '40' },
              { offset: 1, color: ship.color + '05' },
            ],
          },
        },
      },
    ],
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
      formatter: '{b}<br/>速度: {c} kn',
    },
  })
}

function renderSOGDistChart() {
  const ship = store.selectedShip
  if (!ship || !sogDistChartEl.value) return

  if (sogDistChart) sogDistChart.dispose()
  sogDistChart = echarts.init(sogDistChartEl.value)

  const ranges = ['0-5', '5-10', '10-15', '15-20', '20+']
  const counts = ranges.map(() => Math.floor(Math.random() * 30) + 5)

  sogDistChart.setOption({
    grid: { top: 10, right: 10, bottom: 24, left: 36 },
    xAxis: {
      type: 'category',
      data: ranges,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisLabel: { color: '#64748b', fontSize: 10 },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [
      {
        data: counts.map((v) => ({
          value: v,
          itemStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: ship.color + 'cc' },
                { offset: 1, color: ship.color + '33' },
              ],
            },
            borderRadius: [3, 3, 0, 0],
          },
        })),
        type: 'bar',
        barWidth: '50%',
      },
    ],
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
      formatter: '{b} kn<br/>数量: {c}',
    },
  })
}

function clearAnalysisItem(item: 'area' | 'distance' | 'prediction' | 'stops' | 'cpa') {
  if (item === 'area') store.areaDetectionResult = null
  if (item === 'distance') store.distanceResult = null
  if (item === 'prediction') store.predictionResult = null
  if (item === 'stops') store.stopDetectionResult = null
  if (item === 'cpa') store.cpaResult = null
}

const hasAnalysis = () =>
  store.areaDetectionResult || store.distanceResult || store.predictionResult || store.stopDetectionResult || store.cpaResult

window.addEventListener('resize', handleResize)
onUnmounted(() => {
  speedChart?.dispose()
  sogDistChart?.dispose()
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <!-- Collapsed toggle -->
  <button
    v-show="!store.rightPanelOpen"
    class="flex-shrink-0 w-6 flex items-center justify-center border-l border-slate-700/50 hover:bg-navy-700 transition cursor-pointer"
    style="background: #111827"
    @click="store.toggleRightPanel()"
  >
    <svg width="14" height="14" fill="none" stroke="#94a3b8" viewBox="0 0 24 24">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  </button>

  <aside
    v-show="store.rightPanelOpen"
    class="flex-shrink-0 overflow-y-auto border-l border-slate-700/50 flex flex-col transition-all duration-300"
    style="width: 360px; background: #111827"
  >
    <!-- Panel Header -->
    <div class="px-4 py-3 border-b border-slate-700/30 flex items-center justify-between">
      <span class="text-xs font-semibold text-slate-400 uppercase tracking-wider">数据分析</span>
      <button
        class="w-6 h-6 rounded-md border border-slate-700 flex items-center justify-center text-slate-400 hover:bg-navy-700 hover:text-white transition"
        @click="store.toggleRightPanel()"
      >
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
    </div>

    <!-- Tabs -->
    <div class="flex border-b border-slate-700/30 px-4">
      <button
        v-for="tab in (['detail', 'stats', 'analysis'] as const)"
        :key="tab"
        class="text-xs py-2.5 px-3 transition"
        :class="
          store.activeRightTab === tab
            ? 'text-ocean-500 border-b-2 border-ocean-500'
            : 'text-slate-500 hover:text-slate-300'
        "
        @click="store.activeRightTab = tab"
      >
        {{ tab === 'detail' ? '船舶详情' : tab === 'stats' ? '航行统计' : '分析结果' }}
      </button>
    </div>

    <!-- Tab Content -->
    <div class="flex-1 overflow-y-auto">
      <!-- ===== Detail Tab ===== -->
      <div v-show="store.activeRightTab === 'detail'" class="px-4 py-3">
        <!-- Empty -->
        <div
          v-if="!store.selectedShip"
          class="flex flex-col items-center justify-center py-16 text-center"
        >
          <div class="w-16 h-16 rounded-full bg-slate-800 flex items-center justify-center mb-4">
            <svg width="28" height="28" fill="none" stroke="#475569" viewBox="0 0 24 24">
              <path d="M2 20a2 2 0 0 0 2-2V8l4-6h8l4 6v10a2 2 0 0 0 2 2" />
              <path d="M12 4v8" />
              <path d="M2 12h20" />
            </svg>
          </div>
          <p class="text-sm text-slate-500 mb-1">未选择船舶</p>
          <p class="text-xs text-slate-600">请在左侧面板搜索或点击地图上的船舶图标</p>
        </div>

        <!-- Ship Detail -->
        <div v-else class="space-y-3">
          <!-- Header Card -->
          <div
            class="rounded-lg bg-gradient-to-r from-ocean-500/10 to-indigo-500/10 border border-ocean-500/20 p-3"
          >
            <div class="flex items-start justify-between">
              <div>
                <h3 class="text-base font-semibold text-white">
                  {{ store.selectedShip.vessel_name }}
                </h3>
                <div class="flex items-center gap-2 mt-1">
                  <span
                    class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-ocean-500/20 text-ocean-400 border border-ocean-500/30"
                  >
                    {{ VESSEL_TYPES[store.selectedShip.vessel_type] || '未知' }}
                  </span>
                  <span class="text-[11px] text-slate-400 font-mono"
                    >MMSI: {{ store.selectedShip.mmsi }}</span
                  >
                </div>
              </div>
              <div class="w-10 h-10 rounded-lg bg-ocean-500/20 flex items-center justify-center">
                <svg
                  width="22"
                  height="22"
                  fill="none"
                  stroke="#0EA5E9"
                  stroke-width="1.5"
                  viewBox="0 0 24 24"
                >
                  <path d="M2 20a2 2 0 0 0 2-2V8l4-6h8l4 6v10a2 2 0 0 0 2 2" />
                  <path d="M12 4v8" />
                  <path d="M2 12h20" />
                </svg>
              </div>
            </div>
          </div>

          <!-- Info Grid -->
          <div class="grid grid-cols-2 gap-2">
            <div class="rounded-lg bg-slate-800/50 p-2.5">
              <span class="text-[10px] text-slate-500 block">IMO</span>
              <span class="text-xs font-mono text-slate-200">{{ store.selectedShip.imo }}</span>
            </div>
            <div class="rounded-lg bg-slate-800/50 p-2.5">
              <span class="text-[10px] text-slate-500 block">呼号</span>
              <span class="text-xs font-mono text-slate-200">{{
                store.selectedShip.call_sign
              }}</span>
            </div>
            <div class="rounded-lg bg-slate-800/50 p-2.5">
              <span class="text-[10px] text-slate-500 block">长度</span>
              <span class="text-xs font-mono text-slate-200"
                >{{ store.selectedShip.length }} m</span
              >
            </div>
            <div class="rounded-lg bg-slate-800/50 p-2.5">
              <span class="text-[10px] text-slate-500 block">宽度</span>
              <span class="text-xs font-mono text-slate-200"
                >{{ store.selectedShip.width }} m</span
              >
            </div>
            <div class="rounded-lg bg-slate-800/50 p-2.5">
              <span class="text-[10px] text-slate-500 block">吃水</span>
              <span class="text-xs font-mono text-slate-200"
                >{{ store.selectedShip.draft }} m</span
              >
            </div>
            <div class="rounded-lg bg-slate-800/50 p-2.5">
              <span class="text-[10px] text-slate-500 block">状态</span>
              <span
                class="text-xs"
                :class="store.selectedShip.status === 0 ? 'text-emerald-400' : 'text-amber-400'"
              >
                {{ NAV_STATUS[store.selectedShip.status] || '未知' }}
              </span>
            </div>
          </div>

          <!-- Current Position -->
          <div class="rounded-lg bg-slate-800/50 p-3">
            <h4 class="text-[11px] text-slate-500 font-medium mb-2">当前位置</h4>
            <div class="grid grid-cols-2 gap-x-4 gap-y-1.5">
              <div class="flex justify-between">
                <span class="text-[11px] text-slate-500">经度</span>
                <span class="text-[11px] font-mono text-slate-200"
                  >{{ store.selectedShip.position.lon.toFixed(4) }}°E</span
                >
              </div>
              <div class="flex justify-between">
                <span class="text-[11px] text-slate-500">纬度</span>
                <span class="text-[11px] font-mono text-slate-200"
                  >{{ store.selectedShip.position.lat.toFixed(4) }}°N</span
                >
              </div>
              <div class="flex justify-between">
                <span class="text-[11px] text-slate-500">航速</span>
                <span class="text-[11px] font-mono text-ocean-400"
                  >{{ store.selectedShip.position.sog }} kn</span
                >
              </div>
              <div class="flex justify-between">
                <span class="text-[11px] text-slate-500">航向</span>
                <span class="text-[11px] font-mono text-slate-200"
                  >{{ store.selectedShip.position.cog }}°</span
                >
              </div>
            </div>
          </div>

          <div class="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2">
            <span class="text-[10px] text-slate-500">最后更新</span>
            <span class="text-[10px] font-mono text-slate-400">{{ formatPositionTime(store.selectedShip.position.timestamp) }}</span>
          </div>
        </div>
      </div>

      <!-- ===== Stats Tab ===== -->
      <div v-show="store.activeRightTab === 'stats'" class="px-4 py-3">
        <div
          v-if="!store.trackStatistics"
          class="flex flex-col items-center justify-center py-16 text-center"
        >
          <div class="w-16 h-16 rounded-full bg-slate-800 flex items-center justify-center mb-4">
            <svg width="28" height="28" fill="none" stroke="#475569" viewBox="0 0 24 24">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
            </svg>
          </div>
          <p class="text-sm text-slate-500 mb-1">暂无统计数据</p>
          <p class="text-xs text-slate-600">请先查询船舶轨迹</p>
        </div>

        <div v-else class="space-y-3">
          <div class="grid grid-cols-2 gap-2">
            <!-- Distance -->
            <div
              class="rounded-xl bg-gradient-to-br from-ocean-500/10 to-ocean-500/5 border border-ocean-500/20 p-3"
            >
              <div class="flex items-center gap-1.5 mb-1">
                <svg width="13" height="13" fill="none" stroke="#0EA5E9" viewBox="0 0 24 24">
                  <path d="M18 20V10" />
                  <path d="M12 20V4" />
                  <path d="M6 20v-6" />
                </svg>
                <span class="text-[10px] text-ocean-400">总距离</span>
              </div>
              <div class="text-lg font-bold font-mono text-white count-up">
                {{ store.trackStatistics.distance }}
              </div>
              <div class="text-[10px] text-slate-500">km</div>
            </div>
            <!-- Duration -->
            <div
              class="rounded-xl bg-gradient-to-br from-emerald-500/10 to-emerald-500/5 border border-emerald-500/20 p-3"
            >
              <div class="flex items-center gap-1.5 mb-1">
                <svg width="13" height="13" fill="none" stroke="#10B981" viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
                <span class="text-[10px] text-emerald-400">航行时间</span>
              </div>
              <div class="text-lg font-bold font-mono text-white count-up">
                {{ store.trackStatistics.duration }}
              </div>
              <div class="text-[10px] text-slate-500">小时</div>
            </div>
            <!-- Max Speed -->
            <div
              class="rounded-xl bg-gradient-to-br from-amber-500/10 to-amber-500/5 border border-amber-500/20 p-3"
            >
              <div class="flex items-center gap-1.5 mb-1">
                <svg width="13" height="13" fill="none" stroke="#F59E0B" viewBox="0 0 24 24">
                  <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
                </svg>
                <span class="text-[10px] text-amber-400">最大速度</span>
              </div>
              <div class="text-lg font-bold font-mono text-white count-up">
                {{ store.trackStatistics.maxSpeed }}
              </div>
              <div class="text-[10px] text-slate-500">kn</div>
            </div>
            <!-- Avg Speed -->
            <div
              class="rounded-xl bg-gradient-to-br from-indigo-500/10 to-indigo-500/5 border border-indigo-500/20 p-3"
            >
              <div class="flex items-center gap-1.5 mb-1">
                <svg width="13" height="13" fill="none" stroke="#6366F1" viewBox="0 0 24 24">
                  <line x1="4" y1="21" x2="4" y2="14" />
                  <line x1="4" y1="10" x2="4" y2="3" />
                  <line x1="12" y1="21" x2="12" y2="12" />
                  <line x1="12" y1="8" x2="12" y2="3" />
                  <line x1="20" y1="21" x2="20" y2="16" />
                  <line x1="20" y1="12" x2="20" y2="3" />
                </svg>
                <span class="text-[10px] text-indigo-400">平均速度</span>
              </div>
              <div class="text-lg font-bold font-mono text-white count-up">
                {{ store.trackStatistics.avgSpeed }}
              </div>
              <div class="text-[10px] text-slate-500">kn</div>
            </div>
          </div>

          <!-- Speed Chart -->
          <div class="rounded-lg bg-slate-800/50 border border-slate-700/30 p-3">
            <h4 class="text-[11px] text-slate-500 font-medium mb-2">速度变化曲线</h4>
            <div ref="speedChartEl" style="width: 100%; height: 180px"></div>
          </div>

          <!-- SOG Distribution -->
          <div class="rounded-lg bg-slate-800/50 border border-slate-700/30 p-3">
            <h4 class="text-[11px] text-slate-500 font-medium mb-2">速度分布</h4>
            <div ref="sogDistChartEl" style="width: 100%; height: 140px"></div>
          </div>
        </div>
      </div>

      <!-- ===== Analysis Tab ===== -->
      <div v-show="store.activeRightTab === 'analysis'" class="px-4 py-3">
        <!-- Empty -->
        <div
          v-if="!hasAnalysis()"
          class="flex flex-col items-center justify-center py-16 text-center"
        >
          <div class="w-16 h-16 rounded-full bg-slate-800 flex items-center justify-center mb-4">
            <svg width="28" height="28" fill="none" stroke="#475569" viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="10" />
              <path d="M12 16v-4" />
              <path d="M12 8h.01" />
            </svg>
          </div>
          <p class="text-sm text-slate-500 mb-1">暂无分析结果</p>
          <p class="text-xs text-slate-600">请使用左侧工具进行分析操作</p>
        </div>

        <!-- Area Detection Result -->
        <div v-if="store.areaDetectionResult" class="space-y-3">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-medium text-slate-300 flex items-center gap-1.5">
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <rect x="3" y="3" width="18" height="18" rx="2" />
              </svg>
              区域检测结果
            </h4>
            <button
              class="text-slate-500 hover:text-slate-300"
              @click="clearAnalysisItem('area')"
            >
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          <div
            class="rounded-lg border p-3"
            :class="
              store.areaDetectionResult.entered
                ? 'border-emerald-500/30 bg-emerald-500/5'
                : 'border-slate-600/50 bg-slate-800/50'
            "
          >
            <div class="flex items-center gap-2 mb-3">
              <div
                class="w-6 h-6 rounded-full flex items-center justify-center"
                :class="
                  store.areaDetectionResult.entered ? 'bg-emerald-500/20' : 'bg-slate-600/30'
                "
              >
                <svg
                  v-if="store.areaDetectionResult.entered"
                  width="14"
                  height="14"
                  fill="none"
                  stroke="#10B981"
                  viewBox="0 0 24 24"
                >
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                <svg
                  v-else
                  width="14"
                  height="14"
                  fill="none"
                  stroke="#94a3b8"
                  viewBox="0 0 24 24"
                >
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </div>
              <span
                class="text-sm font-medium"
                :class="
                  store.areaDetectionResult.entered ? 'text-emerald-400' : 'text-slate-400'
                "
              >
                {{ store.areaDetectionResult.entered ? '已进入指定区域' : '未进入指定区域' }}
              </span>
            </div>
            <div v-if="store.areaDetectionResult.entered" class="space-y-2 text-[11px]">
              <div class="flex justify-between">
                <span class="text-slate-500">船舶</span>
                <span class="text-slate-200">{{ store.areaDetectionResult.shipName }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">进入时间</span>
                <span class="font-mono text-slate-200">{{ store.areaDetectionResult.enterTime || '-' }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">离开时间</span>
                <span class="font-mono text-slate-200">{{ store.areaDetectionResult.exitTime || '-' }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">停留时长</span>
                <span class="font-mono text-ocean-400">{{ store.areaDetectionResult.stayDuration || '-' }}</span>
              </div>
            </div>
            <div v-else class="text-[11px] text-slate-500">
              {{ store.areaDetectionResult.shipName }}
              在查询时间段内未进入您绘制的区域范围。
            </div>
          </div>
        </div>

        <!-- Distance Result -->
        <div v-if="store.distanceResult" class="space-y-3 mt-3">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-medium text-slate-300 flex items-center gap-1.5">
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="10" />
              </svg>
              两船距离结果
            </h4>
            <button
              class="text-slate-500 hover:text-slate-300"
              @click="clearAnalysisItem('distance')"
            >
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          <div class="rounded-lg border border-slate-700/50 bg-slate-800/50 p-3">
            <div class="space-y-2 text-[11px]">
              <div class="flex items-center gap-2 mb-3">
                <div
                  class="w-3 h-3 rounded-full"
                  :style="{ background: store.distanceResult.ship1Color }"
                ></div>
                <span class="text-slate-200">{{ store.distanceResult.ship1Name }}</span>
                <svg
                  width="16"
                  height="16"
                  fill="none"
                  stroke="#475569"
                  viewBox="0 0 24 24"
                >
                  <line x1="5" y1="12" x2="19" y2="12" />
                  <polyline points="12 5 19 12 12 19" />
                </svg>
                <div
                  class="w-3 h-3 rounded-full"
                  :style="{ background: store.distanceResult.ship2Color }"
                ></div>
                <span class="text-slate-200">{{ store.distanceResult.ship2Name }}</span>
              </div>
              <div
                class="rounded-lg bg-amber-500/10 border border-amber-500/20 p-3 text-center"
              >
                <div class="text-2xl font-bold font-mono text-amber-400">
                  {{ store.distanceResult.distance.toFixed(2) }}
                </div>
                <div class="text-[10px] text-amber-500/70 mt-1">当前距离 (km)</div>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">最近距离</span>
                <span class="font-mono text-slate-200"
                  >{{ store.distanceResult.minDistance.toFixed(2) }} km</span
                >
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">最近时间</span>
                <span class="font-mono text-slate-200">{{ store.distanceResult.minDistanceTime || '-' }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">当前距离</span>
                <span class="font-mono text-amber-400"
                  >{{ store.distanceResult.distance.toFixed(2) }} km</span
                >
              </div>
            </div>
          </div>
        </div>

        <!-- Prediction Result -->
        <div v-if="store.predictionResult" class="space-y-3 mt-3">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-medium text-slate-300 flex items-center gap-1.5">
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
              </svg>
              轨迹预测结果
            </h4>
            <button
              class="text-slate-500 hover:text-slate-300"
              @click="clearAnalysisItem('prediction')"
            >
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          <div class="rounded-lg border border-slate-700/50 bg-slate-800/50 p-3">
            <div class="space-y-2 text-[11px]">
              <div class="flex items-center gap-2 mb-2">
                <span class="text-slate-200 font-medium">{{
                  store.predictionResult.shipName
                }}</span>
                <span
                  class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-500/20 text-amber-400 border border-amber-500/30"
                  >线性外推</span
                >
              </div>
              <div class="grid grid-cols-2 gap-2">
                <div class="rounded-lg bg-slate-700/30 p-2.5 text-center">
                  <div class="text-base font-bold font-mono text-amber-400">
                    {{ (store.predictionResult.confidence * 100).toFixed(0) }}%
                  </div>
                  <div class="text-[9px] text-slate-500 mt-0.5">置信度</div>
                </div>
                <div class="rounded-lg bg-slate-700/30 p-2.5 text-center">
                  <div class="text-base font-bold font-mono text-slate-200">{{ store.predictionResult.method }}</div>
                  <div class="text-[9px] text-slate-500 mt-0.5">预测方法</div>
                </div>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">预测点数</span>
                <span class="font-mono text-slate-200"
                  >{{ store.predictionResult.points }} 个</span
                >
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">预测终点</span>
                <span class="font-mono text-slate-200"
                  >{{ store.predictionResult.endPoint[0].toFixed(3) }}°E,
                  {{ store.predictionResult.endPoint[1].toFixed(3) }}°N</span
                >
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">当前航速</span>
                <span class="font-mono text-ocean-400"
                  >{{ store.selectedShip?.position.sog ?? '-' }} kn</span
                >
              </div>
              <div class="flex justify-between">
                <span class="text-slate-500">当前航向</span>
                <span class="font-mono text-slate-200"
                  >{{ store.selectedShip?.position.cog ?? '-' }}°</span
                >
              </div>
              <div class="mt-2 p-2 rounded bg-amber-500/5 border border-amber-500/10">
                <p class="text-[10px] text-amber-400/70">
                  ⚠ 预测结果仅供参考，实际航行可能受风浪、洋流、避让等因素影响。
                </p>
              </div>
            </div>
          </div>
        </div>

        <!-- Stop Detection Result -->
        <div v-if="store.stopDetectionResult" class="space-y-3 mt-3">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-medium text-slate-300 flex items-center gap-1.5">
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="3" />
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
              </svg>
              停留点检测结果
            </h4>
            <button
              class="text-slate-500 hover:text-slate-300"
              @click="clearAnalysisItem('stops')"
            >
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          <div class="rounded-lg border border-orange-500/20 bg-orange-500/5 p-3">
            <div class="flex items-center gap-2 mb-3">
              <div class="w-6 h-6 rounded-full bg-orange-500/20 flex items-center justify-center">
                <svg width="14" height="14" fill="none" stroke="#F97316" viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
              </div>
              <span class="text-sm font-medium text-orange-400">
                {{ store.stopDetectionResult.stopCount }} 个停留点
              </span>
            </div>
            <div class="text-[11px] text-slate-400 mb-2">
              总停留时长: {{ Math.round(store.stopDetectionResult.totalDurationMinutes) }} 分钟
            </div>
            <div class="space-y-2 max-h-60 overflow-y-auto">
              <div
                v-for="(stop, index) in store.stopDetectionResult.stops"
                :key="index"
                class="rounded-lg bg-slate-800/50 p-2.5 border border-slate-700/30"
              >
                <div class="flex items-center justify-between mb-1">
                  <span class="text-xs font-medium text-slate-300">停留点 #{{ index + 1 }}</span>
                  <span class="text-[10px] text-orange-400">
                    {{ Math.floor(stop.durationMinutes / 60) }}h {{ Math.round(stop.durationMinutes % 60) }}m
                  </span>
                </div>
                <div class="text-[10px] text-slate-500">
                  <div>{{ new Date(stop.startTime).toLocaleString() }} - {{ new Date(stop.endTime).toLocaleTimeString() }}</div>
                  <div class="mt-0.5 font-mono text-slate-400">
                    {{ stop.lon.toFixed(4) }}°E, {{ stop.lat.toFixed(4) }}°N
                  </div>
                  <div class="mt-0.5 text-slate-600">{{ stop.pointCount }} 个轨迹点</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- CPA Result -->
        <div v-if="store.cpaResult" class="space-y-3 mt-3">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-medium text-slate-300 flex items-center gap-1.5">
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              最近接近点分析 (CPA)
            </h4>
            <button
              class="text-slate-500 hover:text-slate-300"
              @click="clearAnalysisItem('cpa')"
            >
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          <div 
            class="rounded-lg border p-3"
            :class="{
              'border-red-500/30 bg-red-500/5': store.cpaResult.safetyStatus === 'danger',
              'border-amber-500/30 bg-amber-500/5': store.cpaResult.safetyStatus === 'warning',
              'border-emerald-500/30 bg-emerald-500/5': store.cpaResult.safetyStatus === 'safe'
            }"
          >
            <!-- Safety Status Badge -->
            <div class="flex items-center justify-center mb-3">
              <span 
                class="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium"
                :class="{
                  'bg-red-500/20 text-red-400 border border-red-500/30': store.cpaResult.safetyStatus === 'danger',
                  'bg-amber-500/20 text-amber-400 border border-amber-500/30': store.cpaResult.safetyStatus === 'warning',
                  'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30': store.cpaResult.safetyStatus === 'safe'
                }"
              >
                {{ store.cpaResult.safetyText }}
              </span>
            </div>
            
            <!-- Distance Display -->
            <div class="text-center mb-3">
              <div 
                class="text-3xl font-bold font-mono"
                :class="{
                  'text-red-400': store.cpaResult.safetyStatus === 'danger',
                  'text-amber-400': store.cpaResult.safetyStatus === 'warning',
                  'text-emerald-400': store.cpaResult.safetyStatus === 'safe'
                }"
              >
                {{ store.cpaResult.minDistanceNm.toFixed(2) }}
              </div>
              <div class="text-[10px] text-slate-500">海里 (nm)</div>
              <div class="text-[10px] text-slate-400">
                {{ Math.round(store.cpaResult.minDistanceM) }} 米
              </div>
            </div>
            
            <!-- CPA Time -->
            <div class="flex justify-between items-center text-[11px] mb-3 p-2 rounded bg-slate-800/50">
              <span class="text-slate-500">最近接近时间</span>
              <span class="font-mono text-slate-200">
                {{ new Date(store.cpaResult.cpaTime).toLocaleString() }}
              </span>
            </div>
            
            <!-- Ships Info -->
            <div class="space-y-2">
              <!-- Ship A -->
              <div class="flex items-center justify-between p-2 rounded bg-slate-800/30">
                <div class="flex items-center gap-2">
                  <div 
                    class="w-3 h-3 rounded-full"
                    :style="{ background: store.ships.find(s => s.mmsi === store.cpaResult?.mmsiA)?.color || '#0EA5E9' }"
                  ></div>
                  <span class="text-xs text-slate-300">{{ store.cpaResult.nameA }}</span>
                </div>
                <span class="text-[10px] text-slate-400">{{ store.cpaResult.sogA.toFixed(1) }} kn</span>
              </div>
              
              <!-- Ship B -->
              <div class="flex items-center justify-between p-2 rounded bg-slate-800/30">
                <div class="flex items-center gap-2">
                  <div 
                    class="w-3 h-3 rounded-full"
                    :style="{ background: store.ships.find(s => s.mmsi === store.cpaResult?.mmsiB)?.color || '#10B981' }"
                  ></div>
                  <span class="text-xs text-slate-300">{{ store.cpaResult.nameB }}</span>
                </div>
                <span class="text-[10px] text-slate-400">{{ store.cpaResult.sogB.toFixed(1) }} kn</span>
              </div>
            </div>
            
            <!-- Positions -->
            <div class="mt-3 pt-3 border-t border-slate-700/30 space-y-1">
              <div class="text-[10px] text-slate-500 mb-1">CPA 时刻位置</div>
              <div class="text-[10px] font-mono text-slate-400">
                A: {{ store.cpaResult.positionA.lon.toFixed(4) }}°E, {{ store.cpaResult.positionA.lat.toFixed(4) }}°N
              </div>
              <div class="text-[10px] font-mono text-slate-400">
                B: {{ store.cpaResult.positionB.lon.toFixed(4) }}°E, {{ store.cpaResult.positionB.lat.toFixed(4) }}°N
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
