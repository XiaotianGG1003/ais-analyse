<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch, nextTick } from 'vue'
import L from 'leaflet'
import 'leaflet-draw'
import 'leaflet.heat'
import { useAppStore } from '@/stores/app'
import { VESSEL_TYPES } from '@/types'
import * as api from '@/api'

const store = useAppStore()

const mapContainer = ref<HTMLDivElement>()
let map: L.Map
const shipMarkers: Record<number, L.Marker> = {}
const trackLayers: Record<number, L.LayerGroup> = {}
let predictionLayer: L.LayerGroup | null = null
let distanceLine: L.LayerGroup | null = null
let stopPointLayers: L.LayerGroup | null = null
let animationLayer: L.LayerGroup | null = null
let animationShipMarker: L.Marker | null = null
let animationTrailLayer: L.Polyline | null = null
let cpaLayer: L.LayerGroup | null = null
let drawnItems: L.FeatureGroup
let drawControl: L.Draw.Rectangle | null = null
let isDrawing = false
let drawAreaPatched = false

const mapZoom = ref(8)
const mapCenterLat = ref(31.0)
const mapCenterLng = ref(122.0)
const trackCount = ref(0)

// ---- Heatmap ----
let heatmapLayer: L.Layer | null = null
const heatmapVisible = ref(false)
const heatmapLoading = ref(false)

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

  drawnItems = new L.FeatureGroup()
  map.addLayer(drawnItems)

  updateMapInfo()
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

// ---- Render Ships ----
function clearShipMarkers() {
  Object.values(shipMarkers).forEach((m) => map.removeLayer(m))
  for (const k of Object.keys(shipMarkers)) delete shipMarkers[Number(k)]
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
;(window as Record<string, unknown>).__selectShip = (mmsi: number) => {
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

  drawControl = new (L.Draw as any).Rectangle(map, {
    showArea: false,
    metric: false,
    shapeOptions: {
      color: '#0EA5E9',
      fillColor: '#0EA5E9',
      fillOpacity: 0.1,
      weight: 2,
      dashArray: '6, 4',
    },
  })
  drawControl.enable()

  map.on(L.Draw.Event.CREATED, onDrawCreated)
}

function onDrawCreated(e: L.DrawEvents.Created) {
  drawnItems.clearLayers()
  drawnItems.addLayer(e.layer)
  stopAreaDraw()
  performAreaDetection((e.layer as L.Rectangle).getBounds())
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
  const bounds = stops.map((s) => [s.lat, s.lon])
  if (bounds.length > 0) {
    map.fitBounds(bounds as L.LatLngExpression[], { padding: [50, 50] })
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
  const trailPoints = frames.slice(trailStart, currentFrameIndex + 1).map(f => [f.lat, f.lon])
  
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

// Watch selection to pan
watch(
  () => store.selectedMMSI,
  (mmsi) => {
    clearStopPoints()
    clearAnimation()
    clearCPA()
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
})

onUnmounted(() => {
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
defineExpose({ queryTrack, toggleAreaDraw, calcDistance, startPrediction, clearAreaDraw, toggleHeatmap, refreshHeatmap })
</script>

<template>
  <main class="flex-1 relative">
    <div ref="mapContainer" class="w-full h-full" style="background: #0a1628"></div>

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
  </main>
</template>
