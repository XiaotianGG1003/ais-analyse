import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Ship, TrackStatistics, AreaDetectionResult, DistanceResultData, PredictionResultData } from '@/types'
import { MOCK_SHIPS } from '@/data/mockData'
import * as api from '@/api'

// 固定调色板，给从后端加载的船舶分配颜色
const PALETTE = [
  '#0EA5E9', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899',
  '#06B6D4', '#F97316', '#14B8A6', '#6366F1', '#EF4444',
]

export const useAppStore = defineStore('app', () => {
  // Ship list & selection
  const ships = ref<Ship[]>([])
  const loading = ref(false)
  const selectedMMSI = ref<number | null>(null)
  const searchKeyword = ref('')

  const selectedShip = computed(() =>
    ships.value.find((s) => s.mmsi === selectedMMSI.value) ?? null,
  )

  const filteredShips = computed(() => {
    const kw = searchKeyword.value.trim().toLowerCase()
    if (!kw) return ships.value
    return ships.value.filter(
      (s) =>
        s.vessel_name.toLowerCase().includes(kw) ||
        String(s.mmsi).includes(kw),
    )
  })

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

  function selectShip(mmsi: number | null) {
    selectedMMSI.value = mmsi
    trackVisible.value = false
    trackGeoJSON.value = null
    trackStatistics.value = null
    areaDetectionResult.value = null
    distanceResult.value = null
    predictionResult.value = null
    stopDetectionResult.value = null
    if (mmsi) {
      rightPanelOpen.value = true
      activeRightTab.value = 'detail'
      // Fetch full detail from API and update the ship in the list
      api.getVesselDetail(mmsi).then((detail) => {
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
      }).catch(() => { /* detail fetch is best-effort */ })
    }
  }

  /** 从后端加载船舶列表，失败则回退到 mock 数据 */
  async function fetchShips() {
    loading.value = true
    try {
      const res = await api.listVessels(1, 100)
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
      if (ships.value.length === 0) throw new Error('empty')
      // 逐个获取最新位置（前20艘）
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
      showToast(`已从数据库加载 ${ships.value.length} 艘船舶`, 'success')
    } catch {
      ships.value = MOCK_SHIPS
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
      const startISO = new Date(timeStart.value).toISOString()
      const endISO = new Date(timeEnd.value).toISOString()

      const [trackRes, statsRes] = await Promise.allSettled([
        api.getVesselTrack(ship.mmsi, startISO, endISO),
        api.getTrackStatistics(ship.mmsi, startISO, endISO),
      ])

      if (trackRes.status === 'fulfilled' && trackRes.value.track) {
        const tr = trackRes.value
        trackGeoJSON.value = tr.track as GeoJSON.LineString
        // 同步到 ship.track 供地图渲染
        const coords = tr.track.coordinates || []
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
        start_time: new Date(timeStart.value).toISOString(),
        end_time: new Date(timeEnd.value).toISOString(),
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

  function toggleLeftPanel() {
    leftPanelOpen.value = !leftPanelOpen.value
  }

  function toggleRightPanel() {
    rightPanelOpen.value = !rightPanelOpen.value
  }

  return {
    ships,
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
    stopDetectionResult,
    heatmapVisible,
    toasts,
    showToast,
    selectShip,
    queryVesselByMMSI,
    fetchShips,
    fetchTrack,
    fetchAreaDetection,
    fetchDistance,
    fetchPrediction,
    fetchStopDetection,
    toggleLeftPanel,
    toggleRightPanel,
  }
})
