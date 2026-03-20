import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Ship, TrackStatistics, AreaDetectionResult, DistanceResultData, PredictionResultData } from '@/types'
import { MOCK_SHIPS } from '@/data/mockData'
import * as api from '@/api'
import type { ManualTrackPoint, SimilarTrackItemData } from '@/api'
import type { PortAnalysisResponse, PortItem } from '@/api'

// 固定调色板，给从后端加载的船舶分配颜色
const PALETTE = [
  '#0EA5E9', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899',
  '#06B6D4', '#F97316', '#14B8A6', '#6366F1', '#EF4444',
]

const MANUAL_TRAJECTORY_DEFAULT_POINTS = 120

function resampleManualPoints(points: ManualTrackPoint[], targetLen: number): ManualTrackPoint[] {
  if (targetLen < 2) return [...points]
  if (points.length < 2) return [...points]

  const deltas: number[] = []
  for (let i = 1; i < points.length; i += 1) {
    const dx = points[i].lon - points[i - 1].lon
    const dy = points[i].lat - points[i - 1].lat
    deltas.push(Math.hypot(dx, dy))
  }

  const dist: number[] = [0]
  for (let i = 0; i < deltas.length; i += 1) {
    dist.push(dist[i] + deltas[i])
  }

  const total = dist[dist.length - 1]
  if (total < 1e-10) {
    return Array.from({ length: targetLen }, () => ({ ...points[0] }))
  }

  const targetDist: number[] = Array.from(
    { length: targetLen },
    (_, i) => (i * total) / (targetLen - 1),
  )

  const out: ManualTrackPoint[] = []
  let j = 0
  for (let i = 0; i < targetDist.length; i += 1) {
    const td = targetDist[i]
    while (j < dist.length - 2 && dist[j + 1] < td) {
      j += 1
    }
    const segLen = Math.max(1e-10, dist[j + 1] - dist[j])
    const ratio = (td - dist[j]) / segLen
    const p0 = points[j]
    const p1 = points[j + 1]
    out.push({
      lon: p0.lon + ratio * (p1.lon - p0.lon),
      lat: p0.lat + ratio * (p1.lat - p0.lat),
    })
  }

  return out
}

export const useAppStore = defineStore('app', () => {
  // Ship list & selection
  const ships = ref<Ship[]>([])
  const vesselTotal = ref(0)
  const vesselPage = ref(1)
  const vesselPageSize = ref(10)
  const loading = ref(false)
  const selectedMMSI = ref<number | null>(null)
  const searchKeyword = ref('')

  const selectedShip = computed(() =>
    ships.value.find((s) => s.mmsi === selectedMMSI.value) ?? null,
  )

  // 列表仅展示当前数据源，避免输入时前端本地实时过滤。
  const filteredShips = computed(() => ships.value)

  // Time range
  const timeStart = ref('')
  const timeEnd = ref('')

  // Track state
  const trackVisible = ref(false)
  const trackGeoJSON = ref<GeoJSON.LineString | null>(null)
  const trackStatistics = ref<TrackStatistics | null>(null)

  // Panel & tab state
  const leftPanelOpen = ref(true)
  const rightPanelOpen = ref(true)
  const activeRightTab = ref<'detail' | 'stats' | 'analysis'>('detail')

  // Area detection
  const areaDrawMode = ref(false)
  const areaDetectionResult = ref<AreaDetectionResult | null>(null)

  // Distance calculation
  const distanceShipA = ref<number | null>(null)
  const distanceShipB = ref<number | null>(null)
  const distanceResult = ref<DistanceResultData | null>(null)

  // Prediction
  const predictionResult = ref<PredictionResultData | null>(null)
  const manualPredictMode = ref(false)
  const similarTracksResult = ref<SimilarTrackItemData[]>([])
  const similarQueryInfo = ref<{
    points: number
    startPoint: [number, number]
    endPoint: [number, number]
  } | null>(null)

  // Heatmap
  const heatmapVisible = ref(false)

  // Stop Detection
  const stopDetectionResult = ref<{
    vesselName: string
    stopCount: number
    totalDurationMinutes: number
    stops: Array<{
      startTime: string
      endTime: string
      durationMinutes: number
      lat: number
      lon: number
      pointCount: number
    }>
  } | null>(null)

  // Animation
  const animationData = ref<{
    mmsi: number
    frames: Array<{
      timestamp: string
      lat: number
      lon: number
      sog: number
      cog: number
    }>
    stepSeconds: number
    currentFrameIndex: number
    isPlaying: boolean
  } | null>(null)
  const animationTimer = ref<number | null>(null)

  // CPA (Closest Point of Approach)
  const cpaResult = ref<{
    mmsiA: number
    nameA: string
    mmsiB: number
    nameB: string
    cpaTime: string
    minDistanceM: number
    minDistanceNm: number
    safetyStatus: 'danger' | 'warning' | 'safe'
    safetyText: string
    positionA: { lon: number; lat: number }
    positionB: { lon: number; lat: number }
    sogA: number
    sogB: number
    shortestLine: { a: { lon: number; lat: number }; b: { lon: number; lat: number } }
  } | null>(null)

  // Density Analysis
  const densityResult = ref<{
    type: 'heatmap' | 'corridors' | 'speed'
    data: any
  } | null>(null)

  // Ports
  const ports = ref<PortItem[]>([])
  const selectedPortId = ref<number | null>(null)
  const portAnalysisResult = ref<PortAnalysisResponse | null>(null)

  // Trajectory Simplification
  const simplifyResult = ref<{
    mmsi: number
    vessel_name: string
    tolerance_m: number
    original_points: number
    simplified_points: number
    compression_rate: number
    original_path: { lon: number; lat: number; timestamp: string }[]
    simplified_path: { lon: number; lat: number; timestamp: string }[]
  } | null>(null)
  
  const simplifyComparison = ref<{
    mmsi: number
    original_points: number
    comparisons: { tolerance_m: number; simplified_points: number; compression_rate: number }[]
  } | null>(null)

  // Toast
  const toasts = ref<{ id: number; message: string; type: string }[]>([])
  let toastId = 0

  function showToast(message: string, type: string = 'info') {
    const id = ++toastId
    toasts.value.push({ id, message, type })
    setTimeout(() => {
      toasts.value = toasts.value.filter((t) => t.id !== id)
    }, 3000)
  }

  const selectedPort = computed(() =>
    ports.value.find((p) => p.id === selectedPortId.value) ?? null,
  )

  async function selectShip(mmsi: number | null) {
    selectedMMSI.value = mmsi
    trackVisible.value = false
    trackGeoJSON.value = null
    trackStatistics.value = null
    areaDetectionResult.value = null
    distanceResult.value = null
    predictionResult.value = null
    similarTracksResult.value = []
    similarQueryInfo.value = null
    stopDetectionResult.value = null
    // Clear animation
    if (animationTimer.value) {
      clearInterval(animationTimer.value)
      animationTimer.value = null
    }
    animationData.value = null
    // Clear CPA
    cpaResult.value = null
    if (mmsi) {
      rightPanelOpen.value = true
      activeRightTab.value = 'detail'
      // Fetch full detail from API and update the ship in the list
      try {
        const detail = await api.getVesselDetail(mmsi)
        const idx = ships.value.findIndex((s) => s.mmsi === mmsi)
        if (idx >= 0 && detail.last_position) {
          const ship = ships.value[idx]
          ship.imo = detail.imo || ''
          ship.call_sign = detail.call_sign || ''
          ship.status = detail.status ?? 0
          ship.draft = detail.draft ?? 0
          if (detail.last_position) {
            ship.position = {
              lon: detail.last_position.longitude,
              lat: detail.last_position.latitude,
              sog: detail.last_position.sog ?? 0,
              cog: detail.last_position.cog ?? 0,
              heading: detail.last_position.cog ?? 0,
              timestamp: detail.last_position.timestamp,
            }
          }
        }
      } catch {
        // detail fetch is best-effort, continue without it
      }
    }
  }

  async function fetchPorts(keyword = '') {
    try {
      const res = await api.listPorts(1, 100, keyword)
      ports.value = res.items
      if (selectedPortId.value && !ports.value.some((p) => p.id === selectedPortId.value)) {
        selectedPortId.value = null
      }
    } catch (e: unknown) {
      showToast('加载港口列表失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  function selectPort(portId: number | null) {
    selectedPortId.value = portId
  }

  async function createPortByBBox(name: string, bbox: api.PortBBox) {
    try {
      const created = await api.createPort(name, bbox)
      ports.value.unshift(created)
      selectedPortId.value = created.id
      showToast(`港口 ${created.name} 创建成功`, 'success')
      return created
    } catch (e: unknown) {
      showToast('创建港口失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
      return null
    }
  }

  async function removePort(portId: number) {
    try {
      await api.deletePort(portId)
      ports.value = ports.value.filter((p) => p.id !== portId)
      if (selectedPortId.value === portId) {
        selectedPortId.value = null
      }
      if (portAnalysisResult.value?.port_id === portId) {
        portAnalysisResult.value = null
      }
      showToast('港口已删除', 'success')
      return true
    } catch (e: unknown) {
      showToast('删除港口失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
      return false
    }
  }

  async function fetchPortAnalysis(portId: number, topN = 5) {
    if (!timeStart.value || !timeEnd.value) {
      showToast('请先选择时间范围', 'warning')
      return
    }
    try {
      const startISO = timeStart.value + ':00Z'
      const endISO = timeEnd.value + ':00Z'
      const res = await api.getPortAnalysis(portId, startISO, endISO, topN)
      portAnalysisResult.value = res
      activeRightTab.value = 'analysis'
      showToast(`港口分析完成：${res.port_name}`, 'success')
    } catch (e: unknown) {
      showToast('港口分析失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  /** 从后端加载船舶列表，失败则回退到 mock 数据 */
  async function fetchShips(page = vesselPage.value, pageSize = vesselPageSize.value) {
    loading.value = true
    try {
      vesselPage.value = page
      vesselPageSize.value = pageSize
      const res = await api.listVessels(page, pageSize)
      vesselTotal.value = res.total
      ships.value = res.items.map((item, i) => ({
        mmsi: item.mmsi,
        vessel_name: item.vessel_name || `Ship ${item.mmsi}`,
        vessel_type: item.vessel_type ?? 0,
        imo: '',
        call_sign: '',
        length: item.length ?? 0,
        width: item.width ?? 0,
        draft: 0,
        status: 0,
        cargo: 0,
        position: { lat: 0, lon: 0, sog: 0, cog: 0, heading: 0, timestamp: null },
        track: [],
        color: PALETTE[i % PALETTE.length],
      }))
      if (ships.value.length === 0 && res.total === 0) throw new Error('empty')
      // 逐个获取最新位置（当前页，最多20艘）
      const batch = ships.value.slice(0, 20)
      const details = await Promise.allSettled(
        batch.map((s) => api.getVesselDetail(s.mmsi)),
      )
      details.forEach((r, i) => {
        if (r.status === 'fulfilled' && r.value.last_position) {
          const d = r.value
          const ship = batch[i]
          ship.imo = d.imo || ''
          ship.call_sign = d.call_sign || ''
          ship.status = d.status ?? 0
          ship.draft = d.draft ?? 0
          ship.position = {
            lon: d.last_position!.longitude,
            lat: d.last_position!.latitude,
            sog: d.last_position!.sog ?? 0,
            cog: d.last_position!.cog ?? 0,
            heading: d.last_position!.cog ?? 0,
            timestamp: d.last_position!.timestamp,
          }
        }
      })
      showToast(`已加载第 ${vesselPage.value} 页，当前 ${ships.value.length} 艘 / 总计 ${vesselTotal.value} 艘`, 'success')
    } catch {
      ships.value = MOCK_SHIPS
      vesselTotal.value = MOCK_SHIPS.length
      vesselPage.value = 1
      vesselPageSize.value = 10
      showToast('后端连接失败，使用演示数据', 'warning')
    } finally {
      loading.value = false
    }
  }

  /** 查询轨迹 */
  async function fetchTrack() {
    const ship = selectedShip.value
    if (!ship || !timeStart.value || !timeEnd.value) return
    showToast(`正在查询 ${ship.vessel_name} 的轨迹…`, 'info')
    try {
      // 直接使用输入的时间，不做时区转换
      const startISO = timeStart.value + ':00Z'
      const endISO = timeEnd.value + ':00Z'

      const [trackRes, statsRes] = await Promise.allSettled([
        api.getVesselTrack(ship.mmsi, startISO, endISO),
        api.getTrackStatistics(ship.mmsi, startISO, endISO),
      ])

      if (trackRes.status === 'fulfilled' && trackRes.value.track) {
        const tr = trackRes.value
        const track = tr.track
        if (!track) return
        trackGeoJSON.value = track as GeoJSON.LineString
        // 同步到 ship.track 供地图渲染
        const coords = tr.track?.coordinates || []
        ship.track = coords.map((c: number[]) => [c[0], c[1]] as [number, number])
        trackVisible.value = true
        showToast(`已加载 ${tr.point_count} 个轨迹点`, 'success')
      } else {
        showToast('未找到该时间段的轨迹数据', 'warning')
      }

      if (statsRes.status === 'fulfilled') {
        const s = statsRes.value
        trackStatistics.value = {
          distance: s.distance_km.toFixed(1),
          duration: s.duration_hours.toFixed(1),
          maxSpeed: s.max_speed_knots.toFixed(1),
          avgSpeed: s.avg_speed_knots.toFixed(1),
          speedSeries: s.speed_series,
        }
        activeRightTab.value = 'stats'
      }
    } catch (e: unknown) {
      showToast('轨迹查询失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  /** 区域检测 */
  async function fetchAreaDetection(areaPoly: GeoJSON.Polygon) {
    const ship = selectedShip.value
    if (!ship || !timeStart.value || !timeEnd.value) return
    showToast('正在进行区域检测…', 'info')
    try {
      const res = await api.detectArea({
        mmsi: ship.mmsi,
        start_time: timeStart.value + ':00Z',
        end_time: timeEnd.value + ':00Z',
        area: areaPoly as unknown as Record<string, unknown>,
      })
      let stayStr: string | null = null
      if (res.stay_duration_minutes != null) {
        const h = Math.floor(res.stay_duration_minutes / 60)
        const m = Math.round(res.stay_duration_minutes % 60)
        stayStr = h > 0 ? `${h}小时${m}分` : `${m}分钟`
      }
      areaDetectionResult.value = {
        entered: res.entered,
        shipName: ship.vessel_name,
        enterTime: res.enter_time,
        exitTime: res.exit_time,
        stayDuration: stayStr,
        insideTrack: res.inside_track,
      }
      activeRightTab.value = 'analysis'
      showToast(res.entered ? '船舶已进入指定区域' : '船舶未进入指定区域', res.entered ? 'success' : 'info')
    } catch (e: unknown) {
      showToast('区域检测失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  /** 两船距离 */
  async function fetchDistance(mmsi1: number, mmsi2: number) {
    showToast('正在计算两船距离…', 'info')
    try {
      const res = await api.calcDistance(mmsi1, mmsi2)
      const s1 = ships.value.find((s) => s.mmsi === mmsi1)
      const s2 = ships.value.find((s) => s.mmsi === mmsi2)
      distanceResult.value = {
        ship1Name: s1?.vessel_name || String(mmsi1),
        ship2Name: s2?.vessel_name || String(mmsi2),
        ship1Color: s1?.color || '#0EA5E9',
        ship2Color: s2?.color || '#10B981',
        distance: res.current_distance_km,
        minDistance: res.min_distance_km,
        minDistanceTime: res.min_distance_time,
      }
      activeRightTab.value = 'analysis'
      showToast(`${s1?.vessel_name} ↔ ${s2?.vessel_name}: ${res.current_distance_km.toFixed(1)} km`, 'success')
    } catch (e: unknown) {
      showToast('距离计算失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  /** 轨迹预测 */
  async function fetchPrediction(durationMinutes = 60) {
    const ship = selectedShip.value
    if (!ship) return
    showToast(`正在为 ${ship.vessel_name} 生成轨迹预测…`, 'info')
    try {
      const res = await api.predictTrajectory(ship.mmsi, durationMinutes)
      const coords = res.predicted_track.coordinates || []
      const endPt = coords.length > 0 ? coords[coords.length - 1] : [0, 0]
      predictionResult.value = {
        shipName: ship.vessel_name,
        confidence: res.confidence,
        points: coords.length,
        endPoint: [endPt[0], endPt[1]] as [number, number],
        predictedTrack: res.predicted_track,
        predictedTimestamps: res.predicted_timestamps,
        method: res.method,
      }
      activeRightTab.value = 'analysis'
      showToast(`置信度: ${(res.confidence * 100).toFixed(0)}%，预测 ${coords.length} 个航路点`, 'success')
    } catch (e: unknown) {
      showToast('轨迹预测失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  async function fetchPredictionFromPoints(points: ManualTrackPoint[], durationMinutes = 60, stepSeconds = 60) {
    if (points.length < 2) {
      showToast('请至少绘制 2 个点', 'warning')
      return
    }

    const fixedPoints = resampleManualPoints(points, MANUAL_TRAJECTORY_DEFAULT_POINTS)
    showToast(`正在基于 ${fixedPoints.length} 个手绘点生成轨迹预测…`, 'info')
    try {
      const res = await api.predictTrajectoryFromPoints(fixedPoints, durationMinutes, stepSeconds)
      const coords = res.predicted_track.coordinates || []
      const endPt = coords.length > 0 ? coords[coords.length - 1] : [0, 0]
      predictionResult.value = {
        shipName: '手绘轨迹',
        confidence: res.confidence,
        points: coords.length,
        endPoint: [endPt[0], endPt[1]] as [number, number],
        predictedTrack: res.predicted_track,
        predictedTimestamps: res.predicted_timestamps,
        method: res.method,
      }
      activeRightTab.value = 'analysis'
      showToast(`手绘轨迹预测完成：${coords.length} 个点`, 'success')
    } catch (e: unknown) {
      showToast('手绘轨迹预测失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  async function fetchSimilarTracksFromPoints(points: ManualTrackPoint[], topK = 5) {
    if (points.length < 2) {
      showToast('请先在地图上至少点击 2 个点', 'warning')
      return []
    }

    const fixedPoints = resampleManualPoints(points, MANUAL_TRAJECTORY_DEFAULT_POINTS)
    const start = fixedPoints[0]
    const end = fixedPoints[fixedPoints.length - 1]
    similarQueryInfo.value = {
      points: fixedPoints.length,
      startPoint: [start.lon, start.lat],
      endPoint: [end.lon, end.lat],
    }

    showToast(`正在检索最相似的 ${topK} 条轨迹…`, 'info')
    try {
      const res = await api.getSimilarTracksFromPoints(fixedPoints, topK)
      similarTracksResult.value = res.tracks || []
      activeRightTab.value = 'analysis'
      showToast(`已找到 ${similarTracksResult.value.length} 条相似轨迹`, 'success')
      return similarTracksResult.value
    } catch (e: unknown) {
      showToast('相似轨迹检索失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
      return []
    }
  }

  /** 轨迹动画 */
  async function fetchAnimationData(stepSeconds = 60) {
    const ship = selectedShip.value
    if (!ship || !timeStart.value || !timeEnd.value) return
    showToast(`正在生成 ${ship.vessel_name} 的轨迹动画…`, 'info')
    try {
      const res = await api.getAnimationFrames(
        ship.mmsi,
        timeStart.value + ':00Z',
        timeEnd.value + ':00Z',
        stepSeconds,
      )
      animationData.value = {
        mmsi: ship.mmsi,
        frames: res.frames,
        stepSeconds: res.step_seconds,
        currentFrameIndex: 0,
        isPlaying: false,
      }
      showToast(`已生成 ${res.frame_count} 帧动画数据`, 'success')
    } catch (e: unknown) {
      showToast('动画数据生成失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  function startAnimation() {
    if (!animationData.value || animationData.value.frames.length === 0) return
    animationData.value.isPlaying = true
    // Start playback timer
    const intervalMs = (animationData.value.stepSeconds * 1000) / 2 // 2x speed
    animationTimer.value = window.setInterval(() => {
      if (!animationData.value) return
      if (animationData.value.currentFrameIndex >= animationData.value.frames.length - 1) {
        // Loop or stop at end
        animationData.value.currentFrameIndex = 0
      } else {
        animationData.value.currentFrameIndex++
      }
    }, intervalMs) as unknown as number
  }

  function pauseAnimation() {
    if (animationTimer.value) {
      clearInterval(animationTimer.value)
      animationTimer.value = null
    }
    if (animationData.value) {
      animationData.value.isPlaying = false
    }
  }

  function stopAnimation() {
    pauseAnimation()
    if (animationData.value) {
      animationData.value.currentFrameIndex = 0
    }
  }

  function setAnimationFrame(index: number) {
    if (!animationData.value) return
    animationData.value.currentFrameIndex = Math.max(0, Math.min(index, animationData.value.frames.length - 1))
  }

  /** 最近接近点分析 (CPA) */
  async function fetchCPA(mmsiA: number, mmsiB: number) {
    const shipA = ships.value.find((s) => s.mmsi === mmsiA)
    const shipB = ships.value.find((s) => s.mmsi === mmsiB)
    if (!shipA || !shipB) {
      showToast('请选择两艘有效的船舶', 'warning')
      return
    }
    showToast(`正在分析 ${shipA.vessel_name} 与 ${shipB.vessel_name} 的最近接近点…`, 'info')
    try {
      const res = await api.analyzeCPA(mmsiA, mmsiB)
      cpaResult.value = {
        mmsiA: res.mmsi_a,
        nameA: res.name_a,
        mmsiB: res.mmsi_b,
        nameB: res.name_b,
        cpaTime: res.cpa_time,
        minDistanceM: res.min_distance_m,
        minDistanceNm: res.min_distance_nm,
        safetyStatus: res.safety_status,
        safetyText: res.safety_text,
        positionA: res.position_a,
        positionB: res.position_b,
        sogA: res.sog_a,
        sogB: res.sog_b,
        shortestLine: res.shortest_line,
      }
      activeRightTab.value = 'analysis'
      showToast(`最小距离: ${res.min_distance_nm} 海里，状态: ${res.safety_text}`, 
        res.safety_status === 'safe' ? 'success' : res.safety_status === 'warning' ? 'warning' : 'error')
    } catch (e: unknown) {
      showToast('CPA 分析失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  /** 轨迹密度分析 */
  async function fetchDensityAnalysis(
    type: 'heatmap' | 'corridors' | 'speed',
    params: { startTime?: string; endTime?: string; gridSize?: number; minVessels?: number },
  ) {
    const typeNames = { heatmap: '热力图', corridors: '航道分析', speed: '速度分析' }
    showToast(`正在生成${typeNames[type]}…`, 'info')
    try {
      let res
      if (type === 'heatmap') {
        res = await api.getDensityHeatmap(params.startTime, params.endTime, params.gridSize)
      } else if (type === 'corridors') {
        res = await api.getBusyCorridors(params.startTime, params.endTime, params.minVessels, params.gridSize)
      } else {
        res = await api.getSpeedAnalysis(params.startTime, params.endTime, params.gridSize)
      }
      densityResult.value = { type, data: res }
      activeRightTab.value = 'analysis'
      showToast(`${typeNames[type]}生成完成`, 'success')
    } catch (e: unknown) {
      showToast(`${typeNames[type]}生成失败: ` + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  function clearDensityResult() {
    densityResult.value = null
  }

  /** 轨迹简化压缩 */
  async function fetchSimplifiedTrajectory(mmsi: number, tolerance: number) {
    const ship = ships.value.find((s) => s.mmsi === mmsi)
    if (!ship) {
      showToast('请选择有效的船舶', 'warning')
      return
    }
    showToast(`正在压缩 ${ship.vessel_name} 的轨迹 (容差: ${tolerance}m)…`, 'info')
    try {
      const res = await api.simplifyTrajectory(mmsi, tolerance)
      simplifyResult.value = {
        mmsi: res.mmsi,
        vessel_name: res.vessel_name,
        tolerance_m: res.tolerance_m,
        original_points: res.original_points,
        simplified_points: res.simplified_points,
        compression_rate: res.compression_rate,
        original_path: res.original_path,
        simplified_path: res.simplified_path,
      }
      simplifyComparison.value = null
      activeRightTab.value = 'analysis'
      showToast(`轨迹压缩完成: ${res.compression_rate}% 压缩率`, 'success')
    } catch (e: unknown) {
      showToast('轨迹压缩失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  async function fetchSimplificationComparison(mmsi: number) {
    const ship = ships.value.find((s) => s.mmsi === mmsi)
    if (!ship) {
      showToast('请选择有效的船舶', 'warning')
      return
    }
    showToast(`正在对比 ${ship.vessel_name} 的不同容差简化效果…`, 'info')
    try {
      const res = await api.compareSimplification(mmsi)
      simplifyComparison.value = {
        mmsi: res.mmsi,
        original_points: res.original_points,
        comparisons: res.comparisons,
      }
      activeRightTab.value = 'analysis'
      showToast('容差对比完成', 'success')
    } catch (e: unknown) {
      showToast('容差对比失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  function clearSimplifyResult() {
    simplifyResult.value = null
    simplifyComparison.value = null
  }

  /** 停留点检测 */
  async function fetchStopDetection(distanceThreshold: number, timeThreshold: number) {
    const ship = selectedShip.value
    if (!ship || !timeStart.value || !timeEnd.value) return
    showToast(`正在检测 ${ship.vessel_name} 的停留点…`, 'info')
    try {
      const res = await api.getStopPoints(
        ship.mmsi,
        distanceThreshold,
        timeThreshold,
        new Date(timeStart.value).toISOString(),
        new Date(timeEnd.value).toISOString(),
      )
      stopDetectionResult.value = {
        vesselName: ship.vessel_name,
        stopCount: res.stop_count,
        totalDurationMinutes: res.total_duration_minutes,
        stops: res.stops.map((s) => ({
          startTime: s.startTime,
          endTime: s.endTime,
          durationMinutes: s.durationMinutes,
          lat: s.lat,
          lon: s.lon,
          pointCount: s.pointCount,
        })),
      }
      activeRightTab.value = 'analysis'
      showToast(`发现 ${res.stop_count} 个停留点，总停留 ${Math.round(res.total_duration_minutes)} 分钟`, 'success')
    } catch (e: unknown) {
      showToast('停留点检测失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    }
  }

  /** 按 MMSI 查询船舶详情并选中 */
  async function queryVesselByMMSI(raw: string) {
    const keyword = raw.trim()
    if (!/^\d{9}$/.test(keyword)) {
      showToast('请输入9位MMSI', 'warning')
      return
    }

    const mmsi = Number(keyword)
    loading.value = true
    try {
      const detail = await api.getVesselDetail(mmsi)

      let ship = ships.value.find((s) => s.mmsi === mmsi)
      if (!ship) {
        ship = {
          mmsi,
          vessel_name: detail.vessel_name || `Ship ${mmsi}`,
          vessel_type: detail.vessel_type ?? 0,
          imo: detail.imo || '',
          call_sign: detail.call_sign || '',
          length: detail.length ?? 0,
          width: detail.width ?? 0,
          draft: detail.draft ?? 0,
          status: detail.status ?? 0,
          cargo: 0,
          position: {
            lon: detail.last_position?.longitude ?? 0,
            lat: detail.last_position?.latitude ?? 0,
            sog: detail.last_position?.sog ?? 0,
            cog: detail.last_position?.cog ?? 0,
            heading: detail.last_position?.cog ?? 0,
            timestamp: detail.last_position?.timestamp ?? null,
          },
          track: [],
          color: PALETTE[ships.value.length % PALETTE.length],
        }
        ships.value.unshift(ship)
      } else {
        ship.vessel_name = detail.vessel_name || ship.vessel_name
        ship.vessel_type = detail.vessel_type ?? ship.vessel_type
        ship.imo = detail.imo || ''
        ship.call_sign = detail.call_sign || ''
        ship.length = detail.length ?? 0
        ship.width = detail.width ?? 0
        ship.draft = detail.draft ?? 0
        ship.status = detail.status ?? 0
        if (detail.last_position) {
          ship.position = {
            lon: detail.last_position.longitude,
            lat: detail.last_position.latitude,
            sog: detail.last_position.sog ?? 0,
            cog: detail.last_position.cog ?? 0,
            heading: detail.last_position.cog ?? 0,
            timestamp: detail.last_position.timestamp,
          }
        }
      }

      selectedMMSI.value = mmsi
      trackVisible.value = false
      trackGeoJSON.value = null
      trackStatistics.value = null
      areaDetectionResult.value = null
      distanceResult.value = null
      predictionResult.value = null
      rightPanelOpen.value = true
      activeRightTab.value = 'detail'
      showToast(`已加载 ${ship.vessel_name} 详情`, 'success')
    } catch (e: unknown) {
      showToast('查询失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    } finally {
      loading.value = false
    }
  }

  /** 后端船舶搜索（MMSI/船名模糊），并刷新左侧列表 */
  async function searchShips(raw: string) {
    const keyword = raw.trim()
    if (!keyword) {
      await fetchShips()
      return
    }

    loading.value = true
    try {
      // 9位 MMSI 走精确查询（详情接口）
      if (/^\d{9}$/.test(keyword)) {
        const mmsi = Number(keyword)
        const detail = await api.getVesselDetail(mmsi)
        ships.value = [{
          mmsi,
          vessel_name: detail.vessel_name || `Ship ${mmsi}`,
          vessel_type: detail.vessel_type ?? 0,
          imo: detail.imo || '',
          call_sign: detail.call_sign || '',
          length: detail.length ?? 0,
          width: detail.width ?? 0,
          draft: detail.draft ?? 0,
          status: detail.status ?? 0,
          cargo: 0,
          position: {
            lon: detail.last_position?.longitude ?? 0,
            lat: detail.last_position?.latitude ?? 0,
            sog: detail.last_position?.sog ?? 0,
            cog: detail.last_position?.cog ?? 0,
            heading: detail.last_position?.cog ?? 0,
            timestamp: detail.last_position?.timestamp ?? null,
          },
          track: [],
          color: PALETTE[0],
        }]
        selectedMMSI.value = mmsi
        rightPanelOpen.value = true
        activeRightTab.value = 'detail'
        showToast('精确查询成功', 'success')
        return
      }

      // 非9位关键词走后端前缀模糊，限制前20条
      const res = await api.searchVessels(keyword, 20)
      ships.value = res.map((item, i) => ({
        mmsi: item.mmsi,
        vessel_name: item.vessel_name || `Ship ${item.mmsi}`,
        vessel_type: item.vessel_type ?? 0,
        imo: '',
        call_sign: '',
        length: item.length ?? 0,
        width: item.width ?? 0,
        draft: 0,
        status: 0,
        cargo: 0,
        position: { lat: 0, lon: 0, sog: 0, cog: 0, heading: 0, timestamp: null },
        track: [],
        color: PALETTE[i % PALETTE.length],
      }))

      if (selectedMMSI.value && !ships.value.some((s) => s.mmsi === selectedMMSI.value)) {
        selectedMMSI.value = null
      }

      showToast(`搜索到 ${ships.value.length} 艘船舶`, ships.value.length > 0 ? 'success' : 'warning')
    } catch (e: unknown) {
      showToast('搜索失败: ' + (e instanceof Error ? e.message : '未知错误'), 'error')
    } finally {
      loading.value = false
    }
  }

  function toggleLeftPanel() {
    leftPanelOpen.value = !leftPanelOpen.value
  }

  function toggleRightPanel() {
    rightPanelOpen.value = !rightPanelOpen.value
  }

  return {
    ships,
    vesselTotal,
    vesselPage,
    vesselPageSize,
    loading,
    selectedMMSI,
    searchKeyword,
    selectedShip,
    filteredShips,
    timeStart,
    timeEnd,
    trackVisible,
    trackGeoJSON,
    trackStatistics,
    leftPanelOpen,
    rightPanelOpen,
    activeRightTab,
    areaDrawMode,
    areaDetectionResult,
    distanceShipA,
    distanceShipB,
    distanceResult,
    predictionResult,
    manualPredictMode,
    similarTracksResult,
    similarQueryInfo,
    stopDetectionResult,
    animationData,
    cpaResult,
    densityResult,
    simplifyResult,
    simplifyComparison,
    ports,
    selectedPortId,
    selectedPort,
    portAnalysisResult,
    heatmapVisible,
    toasts,
    showToast,
    selectShip,
    searchShips,
    queryVesselByMMSI,
    fetchShips,
    fetchTrack,
    fetchAreaDetection,
    fetchDistance,
    fetchPrediction,
    fetchPredictionFromPoints,
    fetchSimilarTracksFromPoints,
    fetchStopDetection,
    fetchAnimationData,
    startAnimation,
    pauseAnimation,
    stopAnimation,
    setAnimationFrame,
    fetchCPA,
    fetchDensityAnalysis,
    clearDensityResult,
    fetchSimplifiedTrajectory,
    fetchSimplificationComparison,
    clearSimplifyResult,
    fetchPorts,
    selectPort,
    createPortByBBox,
    removePort,
    fetchPortAnalysis,
    toggleLeftPanel,
    toggleRightPanel,
  }
})
