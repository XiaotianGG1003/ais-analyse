<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useAppStore } from '@/stores/app'
import { VESSEL_TYPES } from '@/types'
import { getImportTask, importAisCsv, importAisCsvByPath, listImportTasks } from '@/api'
import type { ImportTaskStatus } from '@/api'

const store = useAppStore()

const distancePanelOpen = ref(false)
const stopPanelOpen = ref(false)
const stopDistance = ref(500)  // meters
const stopTime = ref(30)       // minutes
const animationPanelOpen = ref(false)
const animationStep = ref(60)  // seconds
const cpaPanelOpen = ref(false)
const cpaShipA = ref<number | null>(null)
const cpaShipB = ref<number | null>(null)
const importDrawerOpen = ref(false)
const activeQuick = ref('1h')
const fileInputRef = ref<HTMLInputElement | null>(null)
const importing = ref(false)
const importPath = ref('')
const importTaskId = ref('')
const importStage = ref('')
const importProgress = ref(0)
const importStatus = ref('idle')
const importCurrentRows = ref(0)
const importTotalRows = ref(0)
const importEtaSeconds = ref<number | null>(null)
const mobilityStage = ref('等待执行')
const mobilityProgress = ref(0)
const mobilityCurrentRows = ref(0)
const mobilityTotalRows = ref(0)
const mobilityEtaSeconds = ref<number | null>(null)
const mobilityStatus = ref('queued')
const pklStage = ref('等待开始')
const pklProgress = ref(0)
const pklEtaSeconds = ref<number | null>(null)
const pklStatus = ref('queued')
const pklSampleCount = ref(0)
const pklOutputPath = ref('')
const importHistory = ref<ImportTaskStatus[]>([])
const shipPage = ref(1)
const shipPageSize = ref(10)
let importPollTimer: number | null = null

const totalShipPages = computed(() => {
  const total = store.vesselTotal
  return Math.max(1, Math.ceil(total / shipPageSize.value))
})

watch(
  () => [store.vesselPage, store.vesselPageSize],
  () => {
    shipPage.value = store.vesselPage
    shipPageSize.value = store.vesselPageSize
  },
  { immediate: true },
)

function prevShipPage() {
  if (shipPage.value > 1) {
    shipPage.value -= 1
    void store.fetchShips(shipPage.value, shipPageSize.value)
  }
}

function nextShipPage() {
  if (shipPage.value < totalShipPages.value) {
    shipPage.value += 1
    void store.fetchShips(shipPage.value, shipPageSize.value)
  }
}

function onShipPageSizeChange() {
  shipPage.value = 1
  void store.fetchShips(shipPage.value, shipPageSize.value)
}

// Init default time range
store.timeStart = '2025-01-01T00:00'
store.timeEnd = '2025-01-02T00:00'

const emit = defineEmits([
  'queryTrack',
  'areaDetect', 
  'predict',
  'calcDistance',
  'toggleHeatmap',
  'detectStops',
  'toggleAnimation',
  'analyzeCpa'
])

function onQueryTrack() {
  if (!store.selectedShip) {
    store.showToast('请先选择船舶', 'warning')
    return
  }
  emit('queryTrack')
}

async function onSearchVessel() {
  await store.searchShips(store.searchKeyword)
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

function onDetectStops() {
  if (!store.selectedShip) {
    store.showToast('请先选择船舶', 'warning')
    return
  }
  stopPanelOpen.value = !stopPanelOpen.value
  if (!stopPanelOpen.value) {
    emit('detectStops', stopDistance.value, stopTime.value)
  }
}

function onToggleAnimation() {
  if (!store.selectedShip) {
    store.showToast('请先选择船舶', 'warning')
    return
  }
  animationPanelOpen.value = !animationPanelOpen.value
  emit('toggleAnimation')
}

async function onLoadAnimation() {
  await store.fetchAnimationData(animationStep.value)
}

function onToggleCPA() {
  cpaPanelOpen.value = !cpaPanelOpen.value
  if (!cpaPanelOpen.value) {
    emit('analyzeCpa')
  }
}

async function onAnalyzeCPA() {
  if (!cpaShipA.value || !cpaShipB.value) {
    store.showToast('请选择两艘船舶', 'warning')
    return
  }
  if (cpaShipA.value === cpaShipB.value) {
    store.showToast('两艘船舶不能相同', 'warning')
    return
  }
  await store.fetchCPA(cpaShipA.value, cpaShipB.value)
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

function onImportClick() {
  fileInputRef.value?.click()
}

function onToggleImportDrawer() {
  importDrawerOpen.value = !importDrawerOpen.value
}

function stopImportPolling() {
  if (importPollTimer !== null) {
    window.clearInterval(importPollTimer)
    importPollTimer = null
  }
}

function formatNumber(value: number) {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function formatEta(seconds: number | null) {
  if (seconds === null || seconds < 0) return '计算中'
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainSeconds = seconds % 60
  if (minutes < 60) return `${minutes} 分 ${remainSeconds} 秒`
  const hours = Math.floor(minutes / 60)
  const remainMinutes = minutes % 60
  return `${hours} 小时 ${remainMinutes} 分`
}

function formatTaskTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function taskStatusClass(status: string) {
  if (status === 'completed') return 'text-emerald-400'
  if (status === 'failed') return 'text-rose-400'
  if (status === 'running') return 'text-ocean-400'
  return 'text-amber-400'
}

function taskStatusLabel(status: string) {
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  if (status === 'running') return '进行中'
  return '排队中'
}

async function loadImportHistory() {
  try {
    importHistory.value = await listImportTasks(6)
  } catch {
  }
}

async function pollImportTask() {
  if (!importTaskId.value) return
  try {
    const task = await getImportTask(importTaskId.value)
    importStatus.value = task.status
    importStage.value = task.stage
    importProgress.value = task.progress
    importCurrentRows.value = task.current_rows
    importTotalRows.value = task.total_rows
    importEtaSeconds.value = task.eta_seconds
    mobilityStage.value = task.mobility_stage
    mobilityProgress.value = task.mobility_progress
    mobilityCurrentRows.value = task.mobility_current_rows
    mobilityTotalRows.value = task.mobility_total_rows
    mobilityEtaSeconds.value = task.mobility_eta_seconds
    mobilityStatus.value = task.mobility_status
    pklStage.value = task.pkl_stage
    pklProgress.value = task.pkl_progress
    pklEtaSeconds.value = task.pkl_eta_seconds
    pklStatus.value = task.pkl_status
    pklSampleCount.value = task.pkl_sample_count
    pklOutputPath.value = task.pkl_output_path || ''
    await loadImportHistory()

    if (task.status === 'completed') {
      stopImportPolling()
      importing.value = false
      const rebuildMsg = task.trips_rebuilt ? '，轨迹表已重建' : ''
      store.showToast(
        `导入成功：已复制 ${task.rows_inserted} 条原始记录；pkl样本 ${task.pkl_sample_count} 条${rebuildMsg}`,
        'success',
      )
    } else if (task.status === 'failed') {
      stopImportPolling()
      importing.value = false
      store.showToast(`导入失败：${task.error || '未知错误'}`, 'error')
    }
  } catch (err: any) {
    stopImportPolling()
    importing.value = false
    store.showToast(`查询导入状态失败：${err.message}`, 'error')
  }
}

function startImportPolling(taskId: string, initialStage: string, initialProgress: number) {
  stopImportPolling()
  importTaskId.value = taskId
  importStage.value = initialStage
  importProgress.value = initialProgress
  importStatus.value = 'running'
  importCurrentRows.value = 0
  importTotalRows.value = 0
  importEtaSeconds.value = null
  mobilityStage.value = '等待执行'
  mobilityProgress.value = 0
  mobilityCurrentRows.value = 0
  mobilityTotalRows.value = 0
  mobilityEtaSeconds.value = null
  mobilityStatus.value = 'queued'
  pklStage.value = '等待开始'
  pklProgress.value = 0
  pklEtaSeconds.value = null
  pklStatus.value = 'queued'
  pklSampleCount.value = 0
  pklOutputPath.value = ''
  importPollTimer = window.setInterval(() => {
    void pollImportTask()
  }, 2000)
  void pollImportTask()
}

async function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  input.value = ''

  importing.value = true
  store.showToast(`已创建导入任务：${file.name}`, 'info')
  try {
    const result = await importAisCsv(file, true)
    await loadImportHistory()
    startImportPolling(result.task_id, result.stage, result.progress)
  } catch (err: any) {
    store.showToast(`导入失败：${err.message}`, 'error')
    importing.value = false
  } finally {
  }
}

async function onImportByPath() {
  const filePath = importPath.value.trim()
  if (!filePath) {
    store.showToast('请输入 CSV 文件完整路径', 'warning')
    return
  }

  importing.value = true
  store.showToast('已创建大文件导入任务，后台开始处理...', 'info')
  try {
    const result = await importAisCsvByPath(filePath, true)
    await loadImportHistory()
    startImportPolling(result.task_id, result.stage, result.progress)
  } catch (err: any) {
    store.showToast(`导入失败：${err.message}`, 'error')
    importing.value = false
  } finally {
  }
}

onMounted(() => {
  void loadImportHistory()
})

onBeforeUnmount(() => {
  stopImportPolling()
})
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
      <div class="relative">
        <svg
          class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500"
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          type="text"
          placeholder="MMSI / 船名（回车搜索）"
          class="w-full pl-8 py-1.5 text-xs rounded-md border border-slate-700 outline-none focus:border-ocean-500"
          style="background: #1a2332; color: #e2e8f0"
          v-model="store.searchKeyword"
          @keyup.enter="onSearchVessel"
        />
      </div>
    </div>

    <!-- Time Range -->
    <div class="px-4 py-3 border-b border-slate-700/20">
      <label class="text-xs text-slate-500 font-medium mb-2 block">时间选择</label>
      <div class="space-y-2">
        <div class="flex items-center">
          <span class="text-[11px] text-slate-500 w-14 flex-shrink-0">起始时间</span>
          <input
            type="datetime-local"
            v-model="store.timeStart"
            class="flex-1 text-xs py-1.5 rounded-md border border-slate-700 outline-none focus:border-ocean-500"
            style="background: #1a2332; color: #e2e8f0"
          />
        </div>
        <div class="flex items-center">
          <span class="text-[11px] text-slate-500 w-14 flex-shrink-0">结束时间</span>
          <input
            type="datetime-local"
            v-model="store.timeEnd"
            class="flex-1 text-xs py-1.5 rounded-md border border-slate-700 outline-none focus:border-ocean-500"
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
                <!-- 数据导入 -->
        <button
          class="text-xs py-2 px-2 border rounded-md flex items-center justify-center gap-1.5 transition"
          :class="
            importDrawerOpen
              ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-400'
              : 'bg-navy-600 border-slate-700 text-slate-300 hover:bg-navy-500'
          "
          @click="onToggleImportDrawer"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" y1="3" x2="12" y2="15" />
          </svg>
          {{ importDrawerOpen ? '关闭数据导入' : '数据导入' }}
        </button>
        
        <!-- 数据导出 -->
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
          @click="onToggleAnimation"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
          轨迹回放
        </button>
        <button
          class="text-xs py-2 px-2 bg-navy-600 border border-slate-700 text-slate-300 rounded-md flex items-center justify-center gap-1.5 hover:bg-navy-500 transition"
          @click="onToggleCPA"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          最近接近点
        </button>
        <button
          class="text-xs py-2 px-2 bg-navy-600 border border-slate-700 text-slate-300 rounded-md flex items-center justify-center gap-1.5 hover:bg-navy-500 transition"
          @click="onDetectStops"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
          </svg>
          停留点检测
        </button>

        <button
          class="text-xs py-2 px-2 border rounded-md flex items-center justify-center gap-1.5 transition"
          :class="
            store.heatmapVisible
              ? 'bg-rose-500/20 border-rose-500/40 text-rose-400'
              : 'bg-navy-600 border-slate-700 text-slate-300 hover:bg-navy-500'
          "
          @click="emit('toggleHeatmap')"
        >
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
          </svg>
          {{ store.heatmapVisible ? '关闭热力图' : '轨迹热力图' }}
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

    <!-- Stop Detection Panel -->
    <div v-if="stopPanelOpen" class="px-4 py-3 border-b border-slate-700/20">
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-slate-500 font-medium">停留点检测参数</label>
        <button class="text-slate-500 hover:text-slate-300" @click="stopPanelOpen = false">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div class="space-y-3">
        <div>
          <div class="flex justify-between mb-1">
            <span class="text-[10px] text-slate-500">距离阈值</span>
            <span class="text-[10px] text-slate-400">{{ stopDistance }} m</span>
          </div>
          <input
            v-model.number="stopDistance"
            type="range"
            min="100"
            max="1000"
            step="100"
            class="w-full accent-ocean-500"
          />
        </div>
        <div>
          <div class="flex justify-between mb-1">
            <span class="text-[10px] text-slate-500">时间阈值</span>
            <span class="text-[10px] text-slate-400">{{ stopTime }} min</span>
          </div>
          <input
            v-model.number="stopTime"
            type="range"
            min="5"
            max="120"
            step="5"
            class="w-full accent-ocean-500"
          />
        </div>
        <button
          class="w-full py-1.5 text-xs font-medium text-white rounded-md"
          style="background: linear-gradient(135deg, #0ea5e9, #0284c7)"
          @click="onDetectStops"
        >
          开始检测
        </button>
      </div>
    </div>

    <!-- Animation Panel -->
    <div v-if="animationPanelOpen" class="px-4 py-3 border-b border-slate-700/20">
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-slate-500 font-medium">轨迹动画回放</label>
        <button class="text-slate-500 hover:text-slate-300" @click="animationPanelOpen = false">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div class="space-y-3">
        <div>
          <div class="flex justify-between mb-1">
            <span class="text-[10px] text-slate-500">时间步长</span>
            <span class="text-[10px] text-slate-400">{{ animationStep }} 秒</span>
          </div>
          <input
            v-model.number="animationStep"
            type="range"
            min="10"
            max="300"
            step="10"
            class="w-full accent-ocean-500"
          />
        </div>
        <button
          class="w-full py-1.5 text-xs font-medium text-white rounded-md"
          style="background: linear-gradient(135deg, #0ea5e9, #0284c7)"
          @click="onLoadAnimation"
        >
          生成动画
        </button>
        <!-- Playback Controls -->
        <div v-if="store.animationData" class="pt-2 border-t border-slate-700/30">
          <div class="flex items-center justify-center gap-3 mb-2">
            <button
              class="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center text-slate-300 hover:bg-slate-600"
              @click="store.stopAnimation()"
            >
              <svg width="12" height="12" fill="currentColor" viewBox="0 0 24 24">
                <rect x="4" y="4" width="16" height="16" />
              </svg>
            </button>
            <button
              v-if="!store.animationData.isPlaying"
              class="w-10 h-10 rounded-full bg-ocean-500 flex items-center justify-center text-white hover:bg-ocean-400"
              @click="store.startAnimation()"
            >
              <svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
            </button>
            <button
              v-else
              class="w-10 h-10 rounded-full bg-amber-500 flex items-center justify-center text-white hover:bg-amber-400"
              @click="store.pauseAnimation()"
            >
              <svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24">
                <rect x="6" y="4" width="4" height="16" />
                <rect x="14" y="4" width="4" height="16" />
              </svg>
            </button>
          </div>
          <!-- Progress Bar -->
          <div class="flex items-center gap-2">
            <span class="text-[10px] text-slate-500 w-8 text-right">
              {{ store.animationData.currentFrameIndex + 1 }}/{{ store.animationData.frames.length }}
            </span>
            <input
              type="range"
              :min="0"
              :max="store.animationData.frames.length - 1"
              :value="store.animationData.currentFrameIndex"
              @input="(e) => store.setAnimationFrame(Number((e.target as HTMLInputElement).value))"
              class="flex-1 accent-ocean-500"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- CPA Panel -->
    <div v-if="cpaPanelOpen" class="px-4 py-3 border-b border-slate-700/20">
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-slate-500 font-medium">最近接近点分析 (CPA)</label>
        <button class="text-slate-500 hover:text-slate-300" @click="cpaPanelOpen = false">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div class="space-y-3">
        <div>
          <span class="text-[10px] text-slate-500 mb-1 block">船舶 A</span>
          <select
            v-model.number="cpaShipA"
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
          <span class="text-[10px] text-slate-500 mb-1 block">船舶 B</span>
          <select
            v-model.number="cpaShipB"
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
          @click="onAnalyzeCPA"
        >
          分析最近接近点
        </button>
      </div>
    </div>

    <!-- Import Panel -->
    <div v-if="importDrawerOpen" class="px-4 py-3 border-b border-slate-700/20">
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-slate-500 font-medium">数据导入</label>
        <button class="text-slate-500 hover:text-slate-300" @click="importDrawerOpen = false">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div class="space-y-3">
        <input
          ref="fileInputRef"
          type="file"
          accept=".csv"
          class="hidden"
          @change="onFileSelected"
        />
      <button
        class="w-full py-2 text-xs font-medium rounded-md flex items-center justify-center gap-2 border transition"
        :class="importing
          ? 'border-slate-600 text-slate-500 cursor-not-allowed bg-slate-800'
          : 'border-emerald-600/50 text-emerald-400 hover:bg-emerald-900/30 bg-transparent'"
        :disabled="importing"
        @click="onImportClick"
      >
        <svg v-if="!importing" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="17 8 12 3 7 8" />
          <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
        <svg v-else class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
        </svg>
        {{ importing ? '执行两阶段处理中...' : '导入 AIS CSV（两阶段）' }}
      </button>
      <p class="text-[10px] text-slate-600 mt-1.5 leading-relaxed">
        阶段1：仅复制 CSV 到 ais_raw；阶段2：清洗/分段/插值后生成 pkl
      </p>
      <div class="mt-3 space-y-2">
        <input
          v-model="importPath"
          type="text"
          placeholder="例如：D:\\AIS\\ais_2025.csv"
          class="w-full text-xs py-1.5 px-2 rounded-md border border-slate-700 outline-none focus:border-ocean-500"
          style="background: #1a2332; color: #e2e8f0"
        />
        <button
          class="w-full py-2 text-xs font-medium rounded-md flex items-center justify-center gap-2 transition"
          :class="importing
            ? 'bg-slate-700 border border-slate-600 text-slate-400 cursor-not-allowed'
            : 'bg-ocean-500/10 border border-ocean-500/40 text-ocean-400 hover:bg-ocean-500/20'"
          :disabled="importing"
          @click="onImportByPath"
        >
          按路径导入大文件
        </button>
      </div>
      <div v-if="importTaskId" class="mt-3 rounded-md border border-slate-700/70 bg-slate-900/50 p-2.5">
        <div class="flex items-center justify-between text-[10px] text-slate-400">
          <span>任务总状态</span>
          <span>{{ importProgress }}%</span>
        </div>
        <div class="mt-1 text-xs text-slate-300">{{ importStage || '等待执行' }}</div>
        <div class="mt-2 h-1.5 w-full overflow-hidden rounded bg-slate-800">
          <div
            class="h-full rounded bg-ocean-500 transition-all duration-500"
            :style="{ width: `${importProgress}%` }"
          />
        </div>
        <div class="mt-2 text-[10px] text-slate-500 break-all">任务ID：{{ importTaskId }}</div>

        <div class="mt-3 rounded border border-slate-800 bg-slate-950/40 px-2 py-2">
          <div class="flex items-center justify-between text-[10px] text-slate-400">
            <span>1) 复制 CSV 到 ais_raw（不清洗）</span>
            <span>{{ mobilityProgress }}%</span>
          </div>
          <div class="mt-1 text-[11px] text-slate-300">{{ mobilityStage }}</div>
          <div class="mt-1 flex items-center justify-between text-[10px] text-slate-500">
            <span>已读行数</span>
            <span>
              {{ formatNumber(mobilityCurrentRows) }}
              <template v-if="mobilityTotalRows > 0"> / {{ formatNumber(mobilityTotalRows) }}</template>
            </span>
          </div>
          <div class="mt-1 flex items-center justify-between text-[10px] text-slate-500">
            <span>预计剩余</span>
            <span>{{ mobilityStatus === 'completed' ? '导入完成' : formatEta(mobilityEtaSeconds) }}</span>
          </div>
          <div class="mt-2 h-1.5 w-full overflow-hidden rounded bg-slate-800">
            <div class="h-full rounded bg-emerald-500 transition-all duration-500" :style="{ width: `${mobilityProgress}%` }" />
          </div>
        </div>

        <div class="mt-3 rounded border border-slate-800 bg-slate-950/40 px-2 py-2">
          <div class="flex items-center justify-between text-[10px] text-slate-400">
            <span>2) 清洗分段并生成 pkl</span>
            <span>{{ pklProgress }}%</span>
          </div>
          <div class="mt-1 text-[11px] text-slate-300">{{ pklStage }}</div>
          <div class="mt-1 flex items-center justify-between text-[10px] text-slate-500">
            <span>预计剩余</span>
            <span>{{ pklStatus === 'completed' ? '处理完成' : formatEta(pklEtaSeconds) }}</span>
          </div>
          <div class="mt-1 flex items-center justify-between text-[10px] text-slate-500">
            <span>样本数量</span>
            <span>{{ formatNumber(pklSampleCount) }}</span>
          </div>
          <div class="mt-2 h-1.5 w-full overflow-hidden rounded bg-slate-800">
            <div class="h-full rounded bg-violet-500 transition-all duration-500" :style="{ width: `${pklProgress}%` }" />
          </div>
          <div v-if="pklOutputPath" class="mt-2 text-[10px] text-slate-500 break-all">{{ pklOutputPath }}</div>
        </div>
      </div>
      <div class="mt-3 rounded-md border border-slate-700/70 bg-slate-900/40 p-2.5">
        <div class="flex items-center justify-between">
          <span class="text-[11px] text-slate-400">最近导入记录</span>
          <button
            class="text-[10px] text-ocean-400 hover:text-ocean-300 transition"
            @click="loadImportHistory"
          >
            刷新
          </button>
        </div>
        <div v-if="importHistory.length === 0" class="mt-2 text-[10px] text-slate-500">
          暂无导入记录
        </div>
        <div v-else class="mt-2 space-y-2">
          <div
            v-for="task in importHistory"
            :key="task.task_id"
            class="rounded border border-slate-800 bg-slate-950/40 px-2 py-2"
          >
            <div class="flex items-center justify-between gap-3">
              <div class="min-w-0 text-[11px] text-slate-300 truncate">
                {{ task.filename || task.source }}
              </div>
              <div class="text-[10px]" :class="taskStatusClass(task.status)">
                {{ taskStatusLabel(task.status) }}
              </div>
            </div>
            <div class="mt-1 text-[10px] text-slate-500 truncate">{{ task.stage }}</div>
            <div class="mt-1 flex items-center justify-between text-[10px] text-slate-500">
              <span>{{ formatTaskTime(task.updated_at) }}</span>
              <span>{{ task.progress }}%</span>
            </div>
            <div class="mt-1 flex items-center justify-between text-[10px] text-slate-600">
              <span>{{ formatNumber(task.current_rows) }}<template v-if="task.total_rows > 0"> / {{ formatNumber(task.total_rows) }}</template></span>
              <span v-if="task.status === 'completed'">{{ formatNumber(task.rows_inserted) }} 行</span>
              <span v-else-if="task.status === 'failed'">失败</span>
              <span v-else>{{ formatEta(task.eta_seconds) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Ship List -->
    <div class="flex-1 min-h-0 px-2 py-2 flex flex-col">
      <div class="flex items-center justify-between px-2 mb-2">
        <span class="text-[11px] text-slate-500 font-medium">船舶列表</span>
        <span class="text-[10px] text-slate-600 font-mono">总计 {{ store.vesselTotal }} 艘</span>
      </div>

      <div class="flex-1 overflow-y-auto space-y-1 pr-1">
        <div
          v-for="ship in store.ships"
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

      <div class="mt-2 pt-2 border-t border-slate-700/30">
        <div class="flex items-center justify-between gap-2">
          <div class="text-[10px] text-slate-500 font-mono">
            第 {{ shipPage }}/{{ totalShipPages }} 页
          </div>
          <div class="flex items-center gap-2">
            <button
              class="w-6 h-6 text-[11px] rounded border border-slate-700 text-slate-300 transition flex items-center justify-center"
              :class="shipPage <= 1 ? 'opacity-40 cursor-not-allowed' : 'hover:bg-navy-600'"
              :disabled="shipPage <= 1"
              @click="prevShipPage"
              title="上一页"
            >
              <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <polyline points="15 18 9 12 15 6" />
              </svg>
            </button>
            <button
              class="w-6 h-6 text-[11px] rounded border border-slate-700 text-slate-300 transition flex items-center justify-center"
              :class="shipPage >= totalShipPages ? 'opacity-40 cursor-not-allowed' : 'hover:bg-navy-600'"
              :disabled="shipPage >= totalShipPages"
              @click="nextShipPage"
              title="下一页"
            >
              <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <polyline points="9 18 15 12 9 6" />
              </svg>
            </button>
            <select
              v-model.number="shipPageSize"
              class="text-[10px] py-1 px-1.5 rounded border border-slate-700 outline-none"
              style="background: #1a2332; color: #e2e8f0"
              @change="onShipPageSizeChange"
            >
              <option :value="10">10/页</option>
              <option :value="20">20/页</option>
              <option :value="50">50/页</option>
            </select>
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
