<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch, nextTick } from 'vue'
import L from 'leaflet'
import 'leaflet-draw'
import 'leaflet.heat'
import { useAppStore } from '@/stores/app'
import { getTrajectoryCenter } from '@/api'
import { VESSEL_TYPES } from '@/types'
import * as api from '@/api'

const store = useAppStore()

const mapContainer = ref<HTMLDivElement>()
let map: L.Map
const shipMarkers: Record<number, L.Marker> = {}
const trackLayers: Record<number, L.LayerGroup> = {}
let predictionLayer: L.LayerGroup | null = null
let distanceLine: L.LayerGroup | null = null
let clickedTrackLayer: L.LayerGroup | null = null
let similarTracksLayer: L.LayerGroup | null = null
let clickedPoints: Array<{ lon: number; lat: number }> = []
let stopPointLayers: L.LayerGroup | null = null
let animationLayer: L.LayerGroup | null = null
let animationShipMarker: L.Marker | null = null
let animationTrailLayer: L.Polyline | null = null
let cpaLayer: L.LayerGroup | null = null
let densityLayer: L.LayerGroup | null = null
let portsLayer: L.LayerGroup | null = null
let creatingPort = false
let createPortName = ''
let drawnItems: L.FeatureGroup
let drawControl: L.Draw.Rectangle | null = null
let isDrawing = false
let isMouseLineDrawing = false
let lastDrawLatLng: L.LatLng | null = null
const MIN_DRAW_DISTANCE_METERS = 20
let lockTrackDrawingForSimilarView = false
let drawAreaPatched = false

const mapZoom = ref(8)
const mapCenterLat = ref(31.0)
const mapCenterLng = ref(122.0)
const trackCount = ref(0)
const clickedTrackCount = ref(0)

// ---- Heatmap ----
let heatmapLayer: L.Layer | null = null
const heatmapVisible = ref(false)
const heatmapLoading = ref(false)
const manualPrepareVisible = ref(false)
const manualPrepareProgress = ref(0)
const manualPrepareStage = ref('处理数据中')
const manualPrepareMessage = ref('处理数据中')
const manualPrepareTaskId = ref('')
const manualPrepareSampleCount = ref(0)
const manualPrepareEtaSeconds = ref<number | null>(null)
let manualPreparePollTimer: number | null = null

// ---- Ship SVG ----
function getShipSVG(color: string, heading: number) {
  const rotation = heading || 0
  return `<svg width="28" height="28" viewBox="0 0 28 28" style="transform:rotate(${rotation}deg)">
    <polygon points="14,2 22,24 14,20 6,24" fill="${color}" stroke="${color}" stroke-width="0.5" opacity="0.9"/>
    <polygon points="14,6 18,20 14,18 10,20" fill="white" opacity="0.3"/>
  </svg>`
}

function createShipIcon(color: string, heading: number) {
  return L.divIcon({
    html: getShipSVG(color, heading),
    className: '',
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  })
}

function patchLeafletDrawAreaTooltip() {
  if (drawAreaPatched) return
  const geometryUtil = (L as unknown as { GeometryUtil?: { readableArea?: (...args: unknown[]) => string } }).GeometryUtil
  if (!geometryUtil?.readableArea) return
  const original = geometryUtil.readableArea
  geometryUtil.readableArea = (...args: unknown[]) => {
    try {
      return original(...args)
    } catch {
      return ''
    }
  }
  drawAreaPatched = true
}

// ---- Init Map ----
function initMap() {
  if (!mapContainer.value) return
  patchLeafletDrawAreaTooltip()
  map = L.map(mapContainer.value, {
    center: [31.0, 122.0],
    zoom: 8,
    zoomControl: false,
    attributionControl: false,
    minZoom: 4,
    maxZoom: 18,
  })

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    subdomains: 'abcd',
    maxZoom: 19,
  }).addTo(map)

  map.on('zoomend moveend', updateMapInfo)
  map.on('mousedown', onMapMouseDownStartTrack)
  map.on('mousemove', onMapMouseMoveTrack)
  map.on('mouseup', onMapMouseUpEndTrack)

  drawnItems = new L.FeatureGroup()
  map.addLayer(drawnItems)

  updateMapInfo()
}

function shouldIgnoreMapClick(e: L.LeafletMouseEvent) {
  const target = e.originalEvent?.target as HTMLElement | null
  if (!target) return false
  return Boolean(
    target.closest('.leaflet-marker-icon') ||
    target.closest('.leaflet-popup') ||
    target.closest('.leaflet-control')
  )
}

function renderClickedTrack() {
  if (clickedTrackLayer) {
    map.removeLayer(clickedTrackLayer)
  }

  const layer = L.layerGroup()
  const latlngs: L.LatLngExpression[] = clickedPoints.map((p) => [p.lat, p.lon])

  if (latlngs.length >= 2) {
    layer.addLayer(
      L.polyline(latlngs, {
        color: '#38BDF8',
        weight: 4,
        opacity: 0.9,
        lineCap: 'round',
        lineJoin: 'round',
      }),
    )
  }

  if (latlngs.length > 0) {
    const start = latlngs[0] as [number, number]
    layer.addLayer(
      L.circleMarker(start, {
        radius: 5,
        fillColor: '#22C55E',
        fillOpacity: 0.95,
        color: '#166534',
        weight: 1,
      }).bindTooltip('起点'),
    )
  }

  layer.addTo(map)
  clickedTrackLayer = layer
}

function onMapMouseDownStartTrack(e: L.LeafletMouseEvent) {
  if (!store.manualPredictMode) return
  if (isDrawing) return
  if (lockTrackDrawingForSimilarView) return
  if (store.predictionResult || predictionLayer) return
  if (shouldIgnoreMapClick(e)) return
  if ((e.originalEvent as MouseEvent).button !== 0) return

  isMouseLineDrawing = true
  lastDrawLatLng = e.latlng
  clickedPoints = [{ lon: e.latlng.lng, lat: e.latlng.lat }]
  clickedTrackCount.value = 1
  renderClickedTrack()
  map.dragging.disable()
}

function onMapMouseMoveTrack(e: L.LeafletMouseEvent) {
  if (!isMouseLineDrawing) return
  if (!lastDrawLatLng) {
    lastDrawLatLng = e.latlng
  }

  if (map.distance(lastDrawLatLng!, e.latlng) < MIN_DRAW_DISTANCE_METERS) return

  clickedPoints.push({ lon: e.latlng.lng, lat: e.latlng.lat })
  clickedTrackCount.value = clickedPoints.length
  lastDrawLatLng = e.latlng
  renderClickedTrack()
}

function onMapMouseUpEndTrack() {
  if (!isMouseLineDrawing) return
  isMouseLineDrawing = false
  lastDrawLatLng = null
  map.dragging.enable()
}

function clearClickedTrack() {
  clickedPoints = []
  clickedTrackCount.value = 0
  lockTrackDrawingForSimilarView = false
  if (clickedTrackLayer) {
    map.removeLayer(clickedTrackLayer)
    clickedTrackLayer = null
  }
}

async function clearAllTracks() {
  clearClickedTrack()

  Object.entries(trackLayers).forEach(([key, layer]) => {
    map.removeLayer(layer)
    delete trackLayers[Number(key)]
  })
  trackCount.value = 0

  if (predictionLayer) {
    map.removeLayer(predictionLayer)
    predictionLayer = null
  }

  if (distanceLine) {
    map.removeLayer(distanceLine)
    distanceLine = null
  }

  if (similarTracksLayer) {
    map.removeLayer(similarTracksLayer)
    similarTracksLayer = null
  }

  store.trackVisible = false
  store.trackGeoJSON = null
  store.trackStatistics = null
  store.predictionResult = null
  store.similarTracksResult = []
  store.distanceResult = null
  store.manualPredictMode = false

  renderShips()
  await setDefaultViewFromTrajectoryExtent()

  store.showToast('已清空所有轨迹', 'success')
}

function updateMapInfo() {
  if (!map) return
  mapZoom.value = map.getZoom()
  const center = map.getCenter()
  mapCenterLat.value = center.lat
  mapCenterLng.value = center.lng
}

function resetMapView() {
  map.setView([31.0, 122.0], 8)
}

function focusTrajectoryCenter(lat: number, lon: number) {
  if (!map) return
  map.setView([lat, lon], Math.max(map.getZoom(), 8))
}

function focusSimilarTrack(coords: number[][]) {
  if (!map || !coords || coords.length < 2) return
  lockTrackDrawingForSimilarView = true
  const latlngs: L.LatLngExpression[] = coords.map((c) => [c[1], c[0]] as [number, number])
  map.fitBounds(L.latLngBounds(latlngs), { padding: [8, 8], maxZoom: 16 })
}

async function setDefaultViewFromTrajectoryExtent() {
  try {
    const center = await getTrajectoryCenter()
    const hasValidBounds =
      Number.isFinite(center.min_latitude)
      && Number.isFinite(center.max_latitude)
      && Number.isFinite(center.min_longitude)
      && Number.isFinite(center.max_longitude)
      && center.min_latitude < center.max_latitude
      && center.min_longitude < center.max_longitude

    if (hasValidBounds) {
      map.fitBounds(
        [
          [center.min_latitude, center.min_longitude],
          [center.max_latitude, center.max_longitude],
        ],
        { padding: [30, 30], maxZoom: 12 },
      )
      return
    }

    if (Number.isFinite(center.latitude) && Number.isFinite(center.longitude)) {
      map.setView([center.latitude, center.longitude], Math.max(map.getZoom(), 8))
    }
  } catch {
    map.setView([31.0, 122.0], 8)
  }
}

// ---- Render Ships ----
function clearShipMarkers() {
  Object.values(shipMarkers).forEach((m) => map.removeLayer(m))
  for (const k of Object.keys(shipMarkers)) delete shipMarkers[Number(k)]
}

function renderPorts() {
  if (!map) return
  if (portsLayer) {
    map.removeLayer(portsLayer)
    portsLayer = null
  }

  const layer = L.layerGroup()
  store.ports.forEach((port) => {
    const latlngs = port.polygon.coordinates[0].map((c: number[]) => [c[1], c[0]] as [number, number])
    const isSelected = store.selectedPortId === port.id
    const polygon = L.polygon(latlngs, {
      color: isSelected ? '#22D3EE' : '#60A5FA',
      weight: isSelected ? 3 : 2,
      fillColor: '#38BDF8',
      fillOpacity: isSelected ? 0.22 : 0.12,
    })

    polygon.bindTooltip(port.name)
    polygon.on('click', () => {
      store.selectPort(port.id)
      focusPort(port.id)
    })
    layer.addLayer(polygon)
  })

  layer.addTo(map)
  portsLayer = layer
}

function focusPort(portId: number) {
  const port = store.ports.find((p) => p.id === portId)
  if (!port || !map) return

  const b = port.bbox
  map.fitBounds(
    [
      [b.min_lat, b.min_lon],
      [b.max_lat, b.max_lon],
    ],
    { padding: [24, 24], maxZoom: 14 },
  )
}

function stopPortCreateMode() {
  creatingPort = false
  createPortName = ''
  if (drawControl) drawControl.disable()
  map.off(L.Draw.Event.CREATED, onPortDrawCreated)
}

async function onPortDrawCreated(e: L.LeafletEvent) {
  const createdEvent = e as unknown as L.DrawEvents.Created
  const rect = createdEvent.layer as L.Rectangle
  const bounds = rect.getBounds()
  const sw = bounds.getSouthWest()
  const ne = bounds.getNorthEast()
  const currentPortName = createPortName

  stopPortCreateMode()

  const created = await store.createPortByBBox(currentPortName, {
    min_lon: sw.lng,
    min_lat: sw.lat,
    max_lon: ne.lng,
    max_lat: ne.lat,
  })

  if (created) {
    await store.fetchPorts()
    renderPorts()
    focusPort(created.id)
  }
}

function startPortCreateMode(name: string) {
  if (!map) return
  const normalizedName = name.trim()
  if (!normalizedName) {
    store.showToast('港口名称不能为空', 'warning')
    return
  }

  stopAreaDraw()
  stopPortCreateMode()
  createPortName = normalizedName
  creatingPort = true
  store.showToast(`请在地图上框选港口区域：${normalizedName}`, 'info')

  const control = new (L.Draw as any).Rectangle(map, {
    shapeOptions: {
      color: '#22D3EE',
      fillColor: '#22D3EE',
      fillOpacity: 0.12,
      weight: 2,
      dashArray: '8, 4',
    },
  })
  drawControl = control
  control.enable()
  map.on(L.Draw.Event.CREATED, onPortDrawCreated)
}

function renderShips(selectedMmsi?: number) {
  clearShipMarkers()
  if (!selectedMmsi) return

  const ship = store.ships.find((s) => s.mmsi === selectedMmsi)
  if (!ship) return

    const marker = L.marker([ship.position.lat, ship.position.lon], {
      icon: createShipIcon(ship.color, ship.position.heading),
      zIndexOffset: 1000,
    }).addTo(map)

    marker.bindPopup(
      `<div style="min-width:200px; font-family:'Inter',sans-serif;">
        <div style="font-size:14px; font-weight:600; color:#111827; margin-bottom:6px;">${ship.vessel_name}</div>
        <div style="display:grid; grid-template-columns:auto 1fr; gap:4px 12px; font-size:12px;">
          <span style="color:#4b5563;">MMSI</span><span style="color:#111827; font-family:monospace; font-weight:500;">${ship.mmsi}</span>
          <span style="color:#4b5563;">类型</span><span style="color:#111827;">${VESSEL_TYPES[ship.vessel_type] || '未知'}</span>
          <span style="color:#4b5563;">航速</span><span style="color:#0284c7; font-family:monospace; font-weight:500;">${ship.position.sog} kn</span>
          <span style="color:#4b5563;">航向</span><span style="color:#111827; font-family:monospace; font-weight:500;">${ship.position.cog}°</span>
        </div>
        <button onclick="window.__selectShip(${ship.mmsi})" style="margin-top:8px; width:100%; padding:6px; background:linear-gradient(135deg,#0EA5E9,#0284C7); color:white; border:none; border-radius:4px; font-size:12px; cursor:pointer;">
          查看详情
        </button>
      </div>`,
      { className: 'ship-popup' },
    )

    marker.on('click', () => store.selectShip(ship.mmsi))
    shipMarkers[ship.mmsi] = marker
}

// Expose selectShip for popup button
;(window as unknown as { __selectShip?: (mmsi: number) => void }).__selectShip = (mmsi: number) => {
  store.selectShip(mmsi)
}

// ---- Draw Track ----
function drawTrack(mmsi: number) {
  const ship = store.ships.find((s) => s.mmsi === mmsi)
  if (!ship) return

  if (trackLayers[mmsi]) {
    map.removeLayer(trackLayers[mmsi])
  }

  const latlngs: L.LatLngExpression[] = ship.track.map((p) => [p[1], p[0]] as [number, number])
  const trackGroup = L.layerGroup()

  // Glow line
  trackGroup.addLayer(
    L.polyline(latlngs, {
      color: ship.color,
      weight: 8,
      opacity: 0.15,
      smoothFactor: 1,
      lineCap: 'round',
    }),
  )

  // Main line
  trackGroup.addLayer(
    L.polyline(latlngs, {
      color: ship.color,
      weight: 3,
      opacity: 0.8,
      smoothFactor: 1,
      lineCap: 'round',
      lineJoin: 'round',
    }),
  )

  // Track points
  latlngs.forEach((ll, i) => {
    if (i === latlngs.length - 1) return
    trackGroup.addLayer(
      L.circleMarker(ll, {
        radius: 2.5,
        fillColor: ship.color,
        fillOpacity: 0.4 + (i / latlngs.length) * 0.6,
        stroke: false,
      }),
    )
  })

  trackGroup.addTo(map)
  trackLayers[mmsi] = trackGroup
  trackCount.value = Object.keys(trackLayers).length
}

// ---- Query Track (called from parent) ----
async function queryTrack() {
  const ship = store.selectedShip
  if (!ship) return

  await store.fetchTrack()
  if (store.trackVisible) {
    drawTrack(ship.mmsi)
  }
}

// ---- Area Detection ----
function toggleAreaDraw() {
  if (isDrawing) {
    stopAreaDraw()
    return
  }
  isDrawing = true
  store.areaDrawMode = true
  store.showToast('请在地图上点击绘制矩形区域', 'info')

  const control = new (L.Draw as any).Rectangle(map, {
    shapeOptions: {
      color: '#0EA5E9',
      fillColor: '#0EA5E9',
      fillOpacity: 0.1,
      weight: 2,
      dashArray: '6, 4',
    },
  })
  drawControl = control
  control.enable()

  map.on(L.Draw.Event.CREATED, onDrawCreated)
}

function onDrawCreated(e: L.LeafletEvent) {
  const createdEvent = e as unknown as L.DrawEvents.Created
  drawnItems.clearLayers()
  drawnItems.addLayer(createdEvent.layer)
  stopAreaDraw()
  performAreaDetection((createdEvent.layer as L.Rectangle).getBounds())
}

function stopAreaDraw() {
  isDrawing = false
  store.areaDrawMode = false
  if (drawControl) drawControl.disable()
  map.off(L.Draw.Event.CREATED, onDrawCreated)
}

async function performAreaDetection(bounds: L.LatLngBounds) {
  const ship = store.selectedShip
  if (!ship) return

  const sw = bounds.getSouthWest()
  const ne = bounds.getNorthEast()
  const polygon: GeoJSON.Polygon = {
    type: 'Polygon',
    coordinates: [[
      [sw.lng, sw.lat],
      [ne.lng, sw.lat],
      [ne.lng, ne.lat],
      [sw.lng, ne.lat],
      [sw.lng, sw.lat],
    ]],
  }
  await store.fetchAreaDetection(polygon)
}

function clearAreaDraw() {
  drawnItems.clearLayers()
  store.areaDetectionResult = null
}

function clearNonPredictionRenderings() {
  Object.entries(trackLayers).forEach(([key, layer]) => {
    map.removeLayer(layer)
    delete trackLayers[Number(key)]
  })
  trackCount.value = 0

  if (distanceLine) {
    map.removeLayer(distanceLine)
    distanceLine = null
  }

  if (similarTracksLayer) {
    map.removeLayer(similarTracksLayer)
    similarTracksLayer = null
  }

  drawnItems.clearLayers()
  stopAreaDraw()

  store.trackVisible = false
  store.trackGeoJSON = null
  store.trackStatistics = null
  store.areaDetectionResult = null
  store.distanceResult = null
  store.similarTracksResult = []
}

function clearNonSimilarRenderings() {
  clearShipMarkers()

  Object.entries(trackLayers).forEach(([key, layer]) => {
    map.removeLayer(layer)
    delete trackLayers[Number(key)]
  })
  trackCount.value = 0

  if (predictionLayer) {
    map.removeLayer(predictionLayer)
    predictionLayer = null
  }

  if (distanceLine) {
    map.removeLayer(distanceLine)
    distanceLine = null
  }

  drawnItems.clearLayers()
  stopAreaDraw()

  store.trackVisible = false
  store.trackGeoJSON = null
  store.trackStatistics = null
  store.areaDetectionResult = null
  store.distanceResult = null
  store.predictionResult = null
}

function clearOtherOperationsForManualPrediction() {
  if (heatmapVisible.value) {
    hideHeatmap()
  }

  if (predictionLayer) {
    map.removeLayer(predictionLayer)
    predictionLayer = null
  }

  if (similarTracksLayer) {
    map.removeLayer(similarTracksLayer)
    similarTracksLayer = null
  }

  if (distanceLine) {
    map.removeLayer(distanceLine)
    distanceLine = null
  }

  Object.entries(trackLayers).forEach(([key, layer]) => {
    map.removeLayer(layer)
    delete trackLayers[Number(key)]
  })
  trackCount.value = 0

  drawnItems.clearLayers()
  stopAreaDraw()

  clearStopPoints()
  clearAnimation()
  clearCPA()

  clearShipMarkers()

  if (clickedTrackLayer) {
    map.removeLayer(clickedTrackLayer)
    clickedTrackLayer = null
  }
  clickedPoints = []
  clickedTrackCount.value = 0
  lockTrackDrawingForSimilarView = false

  store.trackVisible = false
  store.trackGeoJSON = null
  store.trackStatistics = null
  store.areaDetectionResult = null
  store.distanceResult = null
  store.predictionResult = null
  store.similarTracksResult = []
  store.similarQueryInfo = null
  store.stopDetectionResult = null
  store.cpaResult = null
  store.heatmapVisible = false

  if (store.animationData) {
    store.stopAnimation()
    store.animationData = null
  }
}

function stopManualPreparePolling() {
  if (manualPreparePollTimer !== null) {
    window.clearInterval(manualPreparePollTimer)
    manualPreparePollTimer = null
  }
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

async function waitForPredictorAssetsTask(taskId: string) {
  manualPrepareVisible.value = true
  manualPrepareTaskId.value = taskId

  return new Promise<void>((resolve, reject) => {
    const poll = async () => {
      try {
        const task = await api.getPredictorAssetsPrepareTask(taskId)
        manualPrepareStage.value = task.stage || 'processing_data'
        manualPrepareProgress.value = task.progress
        manualPrepareMessage.value = task.message || '处理数据中'
        manualPrepareSampleCount.value = task.sample_count || 0
        manualPrepareEtaSeconds.value = task.eta_seconds

        if (task.status === 'completed') {
          stopManualPreparePolling()
          manualPrepareVisible.value = false
          manualPrepareTaskId.value = ''
          resolve()
          return
        }

        if (task.status === 'failed') {
          stopManualPreparePolling()
          manualPrepareVisible.value = false
          manualPrepareTaskId.value = ''
          reject(new Error(task.error || '预测依赖文件处理失败'))
        }
      } catch (err) {
        stopManualPreparePolling()
        manualPrepareVisible.value = false
        manualPrepareTaskId.value = ''
        reject(err)
      }
    }

    stopManualPreparePolling()
    manualPreparePollTimer = window.setInterval(() => {
      void poll()
    }, 2000)
    void poll()
  })
}

async function ensureManualPredictorAssetsReady() {
  manualPrepareProgress.value = 0
  manualPrepareStage.value = 'processing_data'
  manualPrepareMessage.value = '处理数据中'
  manualPrepareSampleCount.value = 0
  manualPrepareEtaSeconds.value = null

  const start = await api.preparePredictorAssets()
  if (start.ready) {
    return true
  }

  if (!start.task_id) {
    throw new Error('未获取到预测依赖处理任务 ID')
  }

  manualPrepareVisible.value = true
  manualPrepareProgress.value = start.progress || 0
  manualPrepareStage.value = start.stage || 'processing_data'
  manualPrepareMessage.value = start.message || '处理数据中'
  manualPrepareEtaSeconds.value = start.eta_seconds ?? null
  await waitForPredictorAssetsTask(start.task_id)
  return true
}

// ---- Distance Calculation ----
async function calcDistance(mmsiA: number, mmsiB: number) {
  const ship1 = store.ships.find((s) => s.mmsi === mmsiA)
  const ship2 = store.ships.find((s) => s.mmsi === mmsiB)
  if (!ship1 || !ship2) return

  await store.fetchDistance(mmsiA, mmsiB)

  if (!store.distanceResult) return
  const dist = store.distanceResult.distance

  // Draw distance line
  if (distanceLine) map.removeLayer(distanceLine)
  const lineGroup = L.layerGroup()

  if (ship1.position.lat && ship2.position.lat) {
    lineGroup.addLayer(
      L.polyline(
        [[ship1.position.lat, ship1.position.lon], [ship2.position.lat, ship2.position.lon]],
        { color: '#F59E0B', weight: 2, dashArray: '8, 6', opacity: 0.8 },
      ),
    )

    const midLat = (ship1.position.lat + ship2.position.lat) / 2
    const midLon = (ship1.position.lon + ship2.position.lon) / 2
    const label = L.divIcon({
      html: `<div style="background:#1e293b; border:1px solid #F59E0B55; padding:2px 8px; border-radius:4px; font-size:11px; color:#F59E0B; font-family:monospace; white-space:nowrap;">${dist.toFixed(1)} km</div>`,
      className: '',
      iconAnchor: [30, 10],
    })
    lineGroup.addLayer(L.marker([midLat, midLon], { icon: label }))
    lineGroup.addTo(map)
    distanceLine = lineGroup

    map.fitBounds(
      [[ship1.position.lat, ship1.position.lon], [ship2.position.lat, ship2.position.lon]],
      { padding: [60, 60] },
    )
  }
}

// ---- Prediction ----
async function startPrediction() {
  const ship = store.selectedShip
  if (!ship) return

  await store.fetchPrediction(60)
  clearNonPredictionRenderings()

  drawPredictionFromStore()
}

function drawPredictionFromStore() {
  if (!store.predictionResult) return
  const pred = store.predictionResult
  const coords = pred.predictedTrack.coordinates || []
  if (coords.length < 2) return

  if (predictionLayer) map.removeLayer(predictionLayer)
  const predGroup = L.layerGroup()

  const latlngs: L.LatLngExpression[] = coords.map(
    (c: number[]) => [c[1], c[0]] as [number, number],
  )

  predGroup.addLayer(
    L.polyline(latlngs, {
      color: '#F59E0B',
      weight: 2.5,
      opacity: 0.7,
      dashArray: '8, 6',
      lineCap: 'round',
    }),
  )

  // Skip first coord (current position), draw predicted points
  coords.slice(1).forEach((c: number[], i: number) => {
    const opacity = 1 - (i / coords.length) * 0.6
    predGroup.addLayer(
      L.circleMarker([c[1], c[0]], {
        radius: 4,
        fillColor: '#F59E0B',
        fillOpacity: opacity * 0.6,
        stroke: true,
        color: '#F59E0B',
        weight: 1,
        opacity: opacity * 0.3,
      }),
    )
  })

  const endCoord = coords[coords.length - 1]
  const endIcon = L.divIcon({
    html: `<div style="width:10px;height:10px;background:#F59E0B;border-radius:50%;border:2px solid #FDE68A;box-shadow:0 0 10px #F59E0B88;"></div>`,
    className: '',
    iconSize: [10, 10],
    iconAnchor: [5, 5],
  })
  predGroup.addLayer(L.marker([endCoord[1], endCoord[0]], { icon: endIcon }))
  predGroup.addTo(map)
  predictionLayer = predGroup
}

function focusPredictionFromStore() {
  if (!map || !store.predictionResult) return
  const coords = store.predictionResult.predictedTrack.coordinates || []
  if (coords.length < 2) return

  const latlngs: L.LatLngExpression[] = coords.map(
    (c: number[]) => [c[1], c[0]] as [number, number],
  )
  map.fitBounds(L.latLngBounds(latlngs), { padding: [20, 20], maxZoom: 16 })
}

function drawSimilarTracksFromStore() {
  if (similarTracksLayer) {
    map.removeLayer(similarTracksLayer)
    similarTracksLayer = null
  }

  const tracks = store.similarTracksResult || []
  if (!tracks.length) return

  const colors = ['#22D3EE', '#34D399', '#A3E635', '#F472B6', '#C084FC']
  const layer = L.layerGroup()
  const allLatLngs: L.LatLngExpression[] = []

  tracks.forEach((item, idx) => {
    const coords = item.track?.coordinates || []
    if (coords.length < 2) return
    const latlngs: L.LatLngExpression[] = coords.map((c: number[]) => [c[1], c[0]] as [number, number])
    allLatLngs.push(...latlngs)
    const color = colors[idx % colors.length]

    layer.addLayer(
      L.polyline(latlngs, {
        color,
        weight: 4,
        opacity: 0.9,
        lineCap: 'round',
      }).bindTooltip(`相似轨迹 #${item.rank}`),
    )
  })

  layer.addTo(map)
  similarTracksLayer = layer

  if (allLatLngs.length > 1) {
    map.fitBounds(L.latLngBounds(allLatLngs), { padding: [4, 4], maxZoom: 15 })
    map.zoomIn(1)
  }
}

async function showSimilarTracks() {
  if (clickedPoints.length < 2) {
    store.showToast('请先点击一段轨迹后再检索相似轨迹', 'warning')
    return
  }
  const queryPoints = [...clickedPoints]
  await store.fetchSimilarTracksFromPoints(queryPoints, 5)
  clearNonSimilarRenderings()
  drawSimilarTracksFromStore()
  renderClickedTrack()
}

async function toggleManualPredictionMode() {
  if (store.selectedShip) {
    store.showToast('当前已选船舶，请使用“轨迹预测”', 'warning')
    return
  }

  if (!store.manualPredictMode) {
    clearOtherOperationsForManualPrediction()
    try {
      await ensureManualPredictorAssetsReady()
    } catch (e: unknown) {
      store.showToast(`预测依赖处理失败: ${e instanceof Error ? e.message : '未知错误'}`, 'error')
      return
    }

    lockTrackDrawingForSimilarView = false
    store.manualPredictMode = true
    store.showToast('处理完成，请绘制轨迹', 'success')
    return
  }

  if (clickedPoints.length < 2) {
    store.showToast('请先在地图上至少点击 2 个点', 'warning')
    return
  }

  const manualPoints = [...clickedPoints]
  store.manualPredictMode = false
  await store.fetchPredictionFromPoints(manualPoints, 60, 60)
  clearNonPredictionRenderings()
  drawPredictionFromStore()
  focusPredictionFromStore()
}

// ---- Stop Detection ----
function renderStopPoints() {
  clearStopPoints()
  if (!store.stopDetectionResult?.stops.length) return

  const stops = store.stopDetectionResult.stops
  stopPointLayers = L.layerGroup()

  stops.forEach((stop, index) => {
    // Circle marker size based on duration
    const radius = Math.max(8, Math.min(20, 8 + stop.durationMinutes / 10))

    const marker = L.circleMarker([stop.lat, stop.lon], {
      radius,
      fillColor: '#F97316',
      color: '#FDBA74',
      weight: 2,
      opacity: 0.8,
      fillOpacity: 0.6,
    }).addTo(stopPointLayers!)

    // Format duration
    const hours = Math.floor(stop.durationMinutes / 60)
    const mins = Math.round(stop.durationMinutes % 60)
    const durationStr = hours > 0 ? `${hours}小时${mins}分` : `${mins}分钟`

    marker.bindPopup(`
      <div style="font-family: 'Inter', sans-serif; min-width: 180px;">
        <div style="font-weight: 600; color: #111827; margin-bottom: 4px;">停留点 #${index + 1}</div>
        <div style="font-size: 11px; color: #374151;">开始: ${new Date(stop.startTime).toLocaleString()}</div>
        <div style="font-size: 11px; color: #374151;">结束: ${new Date(stop.endTime).toLocaleString()}</div>
        <div style="font-size: 11px; color: #374151; margin-top: 4px;">时长: <span style="color: #ea580c; font-weight: 600;">${durationStr}</span></div>
        <div style="font-size: 10px; color: #6b7280; margin-top: 2px;">${stop.pointCount} 个轨迹点</div>
      </div>
    `)
  })

  stopPointLayers.addTo(map)

  // Fit bounds to show all stops
  const bounds: L.LatLngTuple[] = stops.map((s) => [s.lat, s.lon])
  if (bounds.length > 0) {
    map.fitBounds(bounds, { padding: [50, 50] })
  }
}

function clearStopPoints() {
  if (stopPointLayers) {
    map.removeLayer(stopPointLayers)
    stopPointLayers = null
  }
}

// ---- Animation ----
function initAnimation() {
  if (!store.animationData) return
  clearAnimation()
  
  animationLayer = L.layerGroup()
  
  // Create ship marker for animation
  const frame = store.animationData.frames[0]
  const ship = store.selectedShip
  const color = ship?.color || '#0EA5E9'
  
  animationShipMarker = L.marker([frame.lat, frame.lon], {
    icon: createShipIcon(color, frame.cog),
    zIndexOffset: 2000,
  }).addTo(animationLayer)
  
  // Add initial tooltip
  animationShipMarker.bindTooltip(
    `<div style="font-size:11px;">
      <div style="font-weight:600;">${ship?.vessel_name || ''}</div>
      <div>${new Date(frame.timestamp).toLocaleTimeString()}</div>
      <div>航速: ${frame.sog.toFixed(1)} kn</div>
    </div>`,
    { permanent: true, direction: 'top', className: 'animation-tooltip' }
  )
  
  animationLayer.addTo(map)
}

function updateAnimationFrame() {
  if (!store.animationData || !animationShipMarker || !animationLayer) return
  
  const { frames, currentFrameIndex } = store.animationData
  const frame = frames[currentFrameIndex]
  if (!frame) return
  
  const ship = store.selectedShip
  const color = ship?.color || '#0EA5E9'
  
  // Update marker position and rotation
  animationShipMarker.setLatLng([frame.lat, frame.lon])
  animationShipMarker.setIcon(createShipIcon(color, frame.cog))
  
  // Update tooltip content
  animationShipMarker.setTooltipContent(
    `<div style="font-size:11px;">
      <div style="font-weight:600;">${ship?.vessel_name || ''}</div>
      <div>${new Date(frame.timestamp).toLocaleTimeString()}</div>
      <div>航速: ${frame.sog.toFixed(1)} kn</div>
    </div>`
  )
  
  // Draw trail (last 10 points)
  const trailStart = Math.max(0, currentFrameIndex - 10)
  const trailPoints: L.LatLngTuple[] = frames
    .slice(trailStart, currentFrameIndex + 1)
    .map((f) => [f.lat, f.lon])
  
  if (animationTrailLayer) {
    animationLayer.removeLayer(animationTrailLayer)
  }
  
  if (trailPoints.length > 1) {
    animationTrailLayer = L.polyline(trailPoints, {
      color: color,
      weight: 3,
      opacity: 0.6,
      dashArray: '5, 5',
    }).addTo(animationLayer)
  }
  
  // Pan map to follow ship
  map.panTo([frame.lat, frame.lon], { animate: true, duration: 0.3 })
}

function clearAnimation() {
  if (animationLayer) {
    map.removeLayer(animationLayer)
    animationLayer = null
  }
  animationShipMarker = null
  animationTrailLayer = null
}

// ---- CPA Visualization ----
function renderCPA() {
  if (!store.cpaResult) return
  clearCPA()
  
  const cpa = store.cpaResult
  cpaLayer = L.layerGroup()
  
  // Ship A position at CPA time
  const shipA = store.ships.find((s) => s.mmsi === cpa.mmsiA)
  const shipB = store.ships.find((s) => s.mmsi === cpa.mmsiB)
  const colorA = shipA?.color || '#0EA5E9'
  const colorB = shipB?.color || '#10B981'
  
  // Marker for Ship A at CPA position
  const markerA = L.circleMarker([cpa.positionA.lat, cpa.positionA.lon], {
    radius: 10,
    fillColor: colorA,
    color: '#fff',
    weight: 2,
    opacity: 1,
    fillOpacity: 0.9,
  }).addTo(cpaLayer)
  
  markerA.bindTooltip(
    `<div style="font-size:11px;">
      <div style="font-weight:600;">${cpa.nameA}</div>
      <div>CPA时刻位置</div>
      <div>航速: ${cpa.sogA.toFixed(1)} kn</div>
    </div>`,
    { permanent: true, direction: 'top', className: 'cpa-tooltip' }
  )
  
  // Marker for Ship B at CPA position
  const markerB = L.circleMarker([cpa.positionB.lat, cpa.positionB.lon], {
    radius: 10,
    fillColor: colorB,
    color: '#fff',
    weight: 2,
    opacity: 1,
    fillOpacity: 0.9,
  }).addTo(cpaLayer)
  
  markerB.bindTooltip(
    `<div style="font-size:11px;">
      <div style="font-weight:600;">${cpa.nameB}</div>
      <div>CPA时刻位置</div>
      <div>航速: ${cpa.sogB.toFixed(1)} kn</div>
    </div>`,
    { permanent: true, direction: 'top', className: 'cpa-tooltip' }
  )
  
  // Shortest line between the two ships at CPA
  const lineColor = cpa.safetyStatus === 'danger' ? '#ef4444' : 
                    cpa.safetyStatus === 'warning' ? '#f59e0b' : '#10b981'
  
  const shortestLine = L.polyline(
    [
      [cpa.positionA.lat, cpa.positionA.lon],
      [cpa.positionB.lat, cpa.positionB.lon],
    ],
    {
      color: lineColor,
      weight: 3,
      opacity: 0.8,
      dashArray: '10, 5',
    }
  ).addTo(cpaLayer)
  
  // Distance label at midpoint
  const midLat = (cpa.positionA.lat + cpa.positionB.lat) / 2
  const midLon = (cpa.positionA.lon + cpa.positionB.lon) / 2
  
  const distanceIcon = L.divIcon({
    html: `<div style="
      background: ${lineColor}; 
      color: white; 
      padding: 2px 6px; 
      border-radius: 4px; 
      font-size: 11px; 
      font-weight: 600;
      white-space: nowrap;
    ">${cpa.minDistanceNm} nm</div>`,
    className: '',
    iconSize: [60, 20],
    iconAnchor: [30, 10],
  })
  
  L.marker([midLat, midLon], { icon: distanceIcon }).addTo(cpaLayer)
  
  cpaLayer.addTo(map)
  
  // Fit bounds to show both positions
  map.fitBounds(
    [
      [cpa.positionA.lat, cpa.positionA.lon],
      [cpa.positionB.lat, cpa.positionB.lon],
    ],
    { padding: [100, 100] }
  )
}

function clearCPA() {
  if (cpaLayer) {
    map.removeLayer(cpaLayer)
    cpaLayer = null
  }
}

// ---- Density Analysis Visualization ----
function renderDensity() {
  if (!store.densityResult) return
  clearDensity()
  
  const { type, data } = store.densityResult
  densityLayer = L.layerGroup()
  
  if (type === 'heatmap') {
    // Render heatmap cells
    data.heatmap.forEach((cell: any) => {
      const intensity = cell.intensity
      const color = intensity > 0.7 ? '#ef4444' : intensity > 0.4 ? '#f59e0b' : '#3b82f6'
      
      const rect = L.rectangle(
        [
          [cell.lat - data.grid_size / 2, cell.lon - data.grid_size / 2],
          [cell.lat + data.grid_size / 2, cell.lon + data.grid_size / 2],
        ],
        {
          fillColor: color,
          fillOpacity: intensity * 0.6,
          color: color,
          weight: 1,
          opacity: 0.3,
        }
      ).addTo(densityLayer!)
      
      rect.bindTooltip(
        `<div style="font-size:11px;">
          <div style="font-weight:600;">轨迹点: ${cell.point_count}</div>
          <div>船舶数: ${cell.vessel_count}</div>
          <div>时间: ${new Date(cell.time_bucket).toLocaleString()}</div>
        </div>`,
        { direction: 'top', className: 'density-tooltip' }
      )
    })
  } else if (type === 'corridors') {
    // Render busy corridors
    data.corridors.forEach((corridor: any) => {
      const intensity = corridor.intensity
      const width = 2 + intensity * 8  // 2-10px width
      const color = intensity > 0.7 ? '#dc2626' : intensity > 0.4 ? '#ea580c' : '#0891b2'
      
      const line = L.polyline(
        [
          [corridor.start.lat, corridor.start.lon],
          [corridor.end.lat, corridor.end.lon],
        ],
        {
          color,
          weight: width,
          opacity: 0.7,
          lineCap: 'round',
          lineJoin: 'round',
        }
      ).addTo(densityLayer!)
      
      line.bindTooltip(
        `<div style="font-size:11px;">
          <div style="font-weight:600;">繁忙航道</div>
          <div>通行次数: ${corridor.passage_count}</div>
          <div>船舶数: ${corridor.unique_vessels}</div>
          <div>平均航速: ${corridor.avg_speed_knots} kn</div>
        </div>`,
        { direction: 'top', className: 'corridor-tooltip' }
      )
    })
  } else if (type === 'speed') {
    // Render speed analysis
    data.speed_data.forEach((cell: any) => {
      const speed = cell.avg_speed_knots
      // Color scale: slow (blue) -> fast (red)
      const color = speed > 20 ? '#dc2626' : 
                    speed > 15 ? '#ea580c' :
                    speed > 10 ? '#f59e0b' :
                    speed > 5 ? '#84cc16' : '#3b82f6'
      
      const circle = L.circleMarker([cell.lat, cell.lon], {
        radius: 6 + Math.min(cell.vessel_count / 5, 8),
        fillColor: color,
        color: '#fff',
        weight: 1,
        opacity: 0.8,
        fillOpacity: 0.6,
      }).addTo(densityLayer!)
      
      circle.bindTooltip(
        `<div style="font-size:11px;">
          <div style="font-weight:600;">平均速度: ${cell.avg_speed_knots} kn</div>
          <div>船舶数: ${cell.vessel_count}</div>
          <div>方差: ${cell.speed_variance}</div>
        </div>`,
        { direction: 'top', className: 'speed-tooltip' }
      )
    })
  }
  
  densityLayer.addTo(map)
}

function clearDensity() {
  if (densityLayer) {
    map.removeLayer(densityLayer)
    densityLayer = null
  }
}

// Watch selection to pan
watch(
  () => store.selectedMMSI,
  (mmsi) => {
    clearStopPoints()
    clearAnimation()
    clearCPA()
    clearDensity()
    if (mmsi && map) {
      const ship = store.ships.find((s) => s.mmsi === mmsi)
      if (ship) {
        // Only render if position is loaded (not the default 0,0)
        const hasValidPosition = ship.position.lat !== 0 || ship.position.lon !== 0
        if (hasValidPosition) {
          renderShips(ship.mmsi)
          map.setView([ship.position.lat, ship.position.lon], Math.max(map.getZoom(), 9))
          drawTrack(ship.mmsi)
        }
      }
    } else {
      clearShipMarkers()
    }
  },
)

// Watch selected ship's position updates (from async detail fetch)
watch(
  () => {
    const ship = store.ships.find((s) => s.mmsi === store.selectedMMSI)
    return ship ? `${ship.position.lat},${ship.position.lon}` : null
  },
  (position) => {
    if (!position || !map) return
    const ship = store.selectedShip
    if (ship) {
      const hasValidPosition = ship.position.lat !== 0 || ship.position.lon !== 0
      if (hasValidPosition && ship.mmsi === store.selectedMMSI) {
        renderShips(ship.mmsi)
        map.setView([ship.position.lat, ship.position.lon], Math.max(map.getZoom(), 9))
        drawTrack(ship.mmsi)
      }
    }
  },
)

// Watch stop detection result
watch(
  () => store.stopDetectionResult,
  (result) => {
    if (result) {
      renderStopPoints()
    } else {
      clearStopPoints()
    }
  },
)

// Watch animation data
watch(
  () => store.animationData,
  (data) => {
    if (data && data.frames.length > 0) {
      initAnimation()
      updateAnimationFrame()
    } else {
      clearAnimation()
    }
  },
)

// Watch animation frame index
watch(
  () => store.animationData?.currentFrameIndex,
  () => {
    updateAnimationFrame()
  },
)

// Watch CPA result
watch(
  () => store.cpaResult,
  (result) => {
    if (result) {
      renderCPA()
    } else {
      clearCPA()
    }
  },
)

// Watch Density Analysis result
watch(
  () => store.densityResult,
  (result) => {
    if (result) {
      renderDensity()
    } else {
      clearDensity()
    }
  },
)

watch(
  () => store.ports,
  () => {
    renderPorts()
  },
  { deep: true },
)

watch(
  () => store.selectedPortId,
  (portId) => {
    renderPorts()
    if (portId) {
      focusPort(portId)
    }
  },
)

// Invalidate size when panels toggle
watch(
  () => [store.leftPanelOpen, store.rightPanelOpen],
  () => {
    setTimeout(() => map?.invalidateSize(), 350)
  },
)

onMounted(async () => {
  await nextTick()
  initMap()
  await store.fetchShips()
  await store.fetchPorts()
  renderPorts()
  await setDefaultViewFromTrajectoryExtent()
  await setDefaultViewFromTrajectoryExtent()
})

onUnmounted(() => {
  stopManualPreparePolling()
  stopPortCreateMode()
  map.off('mousedown', onMapMouseDownStartTrack)
  map.off('mousemove', onMapMouseMoveTrack)
  map.off('mouseup', onMapMouseUpEndTrack)
  if (map) map.remove()
})

// ---- Heatmap Functions ----
async function toggleHeatmap() {
  if (heatmapVisible.value) {
    hideHeatmap()
  } else {
    await showHeatmap()
  }
}

async function showHeatmap() {
  if (!map) return
  
  heatmapLoading.value = true
  store.showToast('正在加载热力图...', 'info')
  
  try {
    const bounds = map.getBounds()
    const data = await api.getTrajectoryHeatmap({
      min_lat: bounds.getSouth(),
      max_lat: bounds.getNorth(),
      min_lon: bounds.getWest(),
      max_lon: bounds.getEast(),
    })
    
    if (data.points.length === 0) {
      store.showToast('当前区域暂无轨迹数据', 'warning')
      heatmapLoading.value = false
      return
    }
    
    // Remove existing heatmap
    if (heatmapLayer) {
      map.removeLayer(heatmapLayer)
    }
    
    // Create heatmap data: [lat, lng, intensity]
    // 添加更多原始点数据以增强热力图密度
    const heatData = data.points.map((p) => [p.lat, p.lon, Math.max(p.intensity, 0.3)] as [number, number, number])
    
    // Create heatmap layer - 优化参数使效果更明显
    heatmapLayer = (L as any).heatLayer(heatData, {
      radius: 35,        // 增大半径，让热力点更明显
      blur: 25,          // 增加模糊度，让渐变更平滑
      maxZoom: 18,       // 在所有缩放级别都显示
      max: 1.0,          // 最大值
      minOpacity: 0.3,   // 最小透明度，让低强度区域也能看到
      gradient: {
        0.0: '#0000FF',  // 深蓝
        0.2: '#00FFFF',  // 青色
        0.4: '#00FF00',  // 绿色
        0.6: '#FFFF00',  // 黄色
        0.8: '#FF8C00',  // 深橙
        1.0: '#FF0000',  // 红色
      },
    }).addTo(map)
    
    heatmapVisible.value = true
    store.showToast(`热力图加载完成，共 ${data.total_points} 个热力点`, 'success')
  } catch (err: any) {
    store.showToast('热力图加载失败: ' + err.message, 'error')
  } finally {
    heatmapLoading.value = false
  }
}

function hideHeatmap() {
  if (heatmapLayer && map) {
    map.removeLayer(heatmapLayer)
    heatmapLayer = null
  }
  heatmapVisible.value = false
}

async function refreshHeatmap() {
  if (heatmapVisible.value) {
    await showHeatmap()
  }
}

// Expose methods for parent
defineExpose({
  queryTrack,
  toggleAreaDraw,
  startPortCreateMode,
  focusPort,
  calcDistance,
  startPrediction,
  clearAreaDraw,
  toggleManualPredictionMode,
  focusTrajectoryCenter,
  focusSimilarTrack,
  toggleHeatmap,
  refreshHeatmap,
})
</script>

<template>
  <main class="flex-1 relative">
    <div ref="mapContainer" class="w-full h-full" style="background: #0a1628"></div>

    <div
      v-if="manualPrepareVisible"
      class="absolute top-16 left-1/2 -translate-x-1/2 z-[1100] w-[360px] rounded-lg border border-slate-700/50 px-4 py-3"
      style="background: rgba(17, 24, 39, 0.95)"
    >
      <div class="flex items-center justify-between text-[11px] text-slate-300 mb-2">
        <span>{{ manualPrepareMessage }}</span>
        <span class="font-mono text-ocean-300">{{ manualPrepareProgress }}%</span>
      </div>
      <div class="h-2 rounded bg-slate-700/50 overflow-hidden">
        <div
          class="h-full bg-ocean-500 transition-all duration-300"
          :style="{ width: `${manualPrepareProgress}%` }"
        ></div>
      </div>
      <div class="mt-2 text-[10px] text-slate-400 flex items-center justify-between">
        <span>阶段：{{ manualPrepareStage }}</span>
        <span>预估剩余（仅供参考）：{{ formatEta(manualPrepareEtaSeconds) }}</span>
      </div>
      <div class="mt-1 text-[10px] text-slate-400 flex items-center justify-end">
        <span v-if="manualPrepareSampleCount > 0">样本：{{ manualPrepareSampleCount }}</span>
      </div>
    </div>

    <!-- Map Overlay: Top info bar -->
    <div class="absolute top-3 left-1/2 -translate-x-1/2 z-[1000] flex items-center gap-3">
      <div
        class="rounded-lg px-4 py-2 border border-slate-700/50 flex items-center gap-4"
        style="background: rgba(17, 24, 39, 0.92)"
      >
        <div class="flex items-center gap-1.5">
          <div class="w-2 h-2 rounded-full bg-emerald-400"></div>
          <span class="text-[11px] text-slate-300">在线船舶</span>
          <span class="text-[11px] font-mono font-semibold text-white">{{
            store.ships.length
          }}</span>
        </div>
        <div class="w-px h-4 bg-slate-700"></div>
        <div class="flex items-center gap-1.5">
          <div class="w-2 h-2 rounded-full bg-ocean-400"></div>
          <span class="text-[11px] text-slate-300">轨迹显示</span>
          <span class="text-[11px] font-mono font-semibold text-white">{{ trackCount }}</span>
        </div>
        <div class="w-px h-4 bg-slate-700"></div>
        <div class="flex items-center gap-1.5">
          <div class="w-2 h-2 rounded-full bg-amber-400"></div>
          <span class="text-[11px] text-slate-300">预警</span>
          <span class="text-[11px] font-mono font-semibold text-amber-400">0</span>
        </div>
      </div>
    </div>

    <!-- Map controls -->
    <div class="absolute top-3 right-3 z-[1000] flex flex-col gap-1.5">
      <button
        class="w-8 h-8 rounded-lg border border-slate-700/50 flex items-center justify-center text-slate-400 hover:text-white transition"
        style="background: rgba(17, 24, 39, 0.92)"
        @click="map?.zoomIn()"
        title="放大"
      >
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      </button>
      <button
        class="w-8 h-8 rounded-lg border border-slate-700/50 flex items-center justify-center text-slate-400 hover:text-white transition"
        style="background: rgba(17, 24, 39, 0.92)"
        @click="map?.zoomOut()"
        title="缩小"
      >
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      </button>
      <div class="w-8 h-px bg-slate-700/50"></div>
      <button
        class="w-8 h-8 rounded-lg border border-slate-700/50 flex items-center justify-center text-slate-400 hover:text-white transition"
        style="background: rgba(17, 24, 39, 0.92)"
        @click="resetMapView"
        title="重置视图"
      >
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
          <path d="M3 3v5h5" />
        </svg>
      </button>
      <button
        class="w-8 h-8 rounded-lg border border-slate-700/50 flex items-center justify-center text-slate-400 hover:text-white transition"
        style="background: rgba(17, 24, 39, 0.92)"
        @click="store.showToast('图层切换成功', 'info')"
        title="图层切换"
      >
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <polygon points="12 2 2 7 12 12 22 7 12 2" />
          <polyline points="2 17 12 22 22 17" />
          <polyline points="2 12 12 17 22 12" />
        </svg>
      </button>
    </div>

    <!-- Scale bar -->
    <div class="absolute bottom-3 left-3 z-[1000]">
      <div
        class="rounded px-2 py-1 border border-slate-700/50 text-[10px] text-slate-500 font-mono"
        style="background: rgba(17, 24, 39, 0.8)"
      >
        缩放: {{ mapZoom }} · {{ mapCenterLat.toFixed(1) }}°N,
        {{ mapCenterLng.toFixed(1) }}°E
      </div>
    </div>

    <div class="absolute bottom-3 right-3 z-[1000] flex items-center gap-2">
      <div
        class="rounded px-2 py-1 border border-slate-700/50 text-[10px] text-slate-400 font-mono"
        style="background: rgba(17, 24, 39, 0.8)"
      >
        点击轨迹点: {{ clickedTrackCount }}
      </div>
      <button
        class="rounded px-2 py-1 border border-slate-700/50 text-[10px] text-slate-300 hover:text-white transition"
        style="background: rgba(17, 24, 39, 0.92)"
        @click="showSimilarTracks"
      >
        显示相似轨迹
      </button>
      <button
        class="w-7 h-7 rounded border border-slate-700/50 text-slate-300 hover:text-white flex items-center justify-center transition"
        style="background: rgba(17, 24, 39, 0.92)"
        title="清空所有轨迹"
        @click="clearAllTracks"
      >
        <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <polyline points="3 6 5 6 21 6" />
          <path d="M19 6l-1 14H6L5 6" />
          <path d="M10 11v6" />
          <path d="M14 11v6" />
          <path d="M9 6V4h6v2" />
        </svg>
      </button>
    </div>
  </main>
</template>
