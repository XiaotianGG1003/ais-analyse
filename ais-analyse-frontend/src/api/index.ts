const BASE = 'http://127.0.0.1:8000/api'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(BASE + url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  const json = await res.json()
  return json.data !== undefined ? json.data : json
}

/* ---- Vessels ---- */

export interface VesselBrief {
  mmsi: number
  vessel_name: string | null
  vessel_type: number | null
  length: number | null
  width: number | null
}

export interface LastPosition {
  longitude: number
  latitude: number
  timestamp: string | null
  sog: number | null
  cog: number | null
}

export interface VesselDetail extends VesselBrief {
  imo: string | null
  call_sign: string | null
  status: number | null
  draft: number | null
  last_position: LastPosition | null
}

export interface VesselListItem extends VesselBrief {
  last_time: string | null
}

export interface VesselListResponse {
  total: number
  page: number
  page_size: number
  items: VesselListItem[]
}

export interface TrackResponse {
  mmsi: number
  vessel_name: string | null
  track: GeoJSON.LineString | null
  timestamps: string[]
  point_count: number
}

export interface TrajectoryCenterData {
  longitude: number
  latitude: number
  min_longitude: number
  max_longitude: number
  min_latitude: number
  max_latitude: number
}

/* ---- Analysis ---- */

export interface TrackStatisticsData {
  mmsi: number
  distance_km: number
  duration_hours: number
  max_speed_knots: number
  avg_speed_knots: number
  speed_series: { time: string; speed: number }[]
}

export interface AreaDetectionResponseData {
  entered: boolean
  enter_time: string | null
  exit_time: string | null
  stay_duration_minutes: number | null
  inside_track: GeoJSON.LineString | null
}

export interface DistanceResponseData {
  mmsi1: number
  mmsi2: number
  current_distance_km: number
  min_distance_km: number
  min_distance_time: string | null
}

export interface PredictionResponseData {
  mmsi: number
  predicted_track: GeoJSON.LineString
  predicted_timestamps: string[]
  confidence: number
  method: string
}

export interface ManualTrackPoint {
  lon: number
  lat: number
}

export interface SimilarTrackItemData {
  rank: number
  global_traj_id: number
  track: GeoJSON.LineString
}

export interface SimilarTracksResponseData {
  tracks: SimilarTrackItemData[]
}

export interface PredictorAssetsStatusData {
  ready: boolean
  sample_pkl_exists: boolean
  index_pkl_exists: boolean
  sample_pkl_path: string | null
  index_pkl_path: string | null
}

export interface PredictorAssetsPrepareStartData {
  ready: boolean
  task_id?: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  stage: string
  progress: number
  message: string
  eta_seconds?: number | null
  sample_pkl_exists?: boolean
  index_pkl_exists?: boolean
  sample_pkl_path?: string | null
  index_pkl_path?: string | null
}

export interface PredictorAssetsPrepareTaskData {
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  stage: string
  progress: number
  message: string
  eta_seconds: number | null
  sample_count: number
  ready: boolean
  sample_pkl_exists: boolean
  index_pkl_exists: boolean
  sample_pkl_path: string | null
  index_pkl_path: string | null
  error: string | null
  created_at: string
  updated_at: string
}

/* ---- API Functions ---- */

export function searchVessels(keyword: string, limit = 20) {
  return request<VesselBrief[]>(
    `/vessels/search?keyword=${encodeURIComponent(keyword)}&limit=${limit}`,
  )
}

export function listVessels(page = 1, pageSize = 100, vesselType?: number) {
  let url = `/vessels?page=${page}&page_size=${pageSize}`
  if (vesselType !== undefined) url += `&vessel_type=${vesselType}`
  return request<VesselListResponse>(url)
}

export function getVesselDetail(mmsi: number) {
  return request<VesselDetail>(`/vessels/${mmsi}`)
}

export function getVesselTrack(mmsi: number, startTime: string, endTime: string) {
  return request<TrackResponse>(
    `/vessels/${mmsi}/track?start_time=${encodeURIComponent(startTime)}&end_time=${encodeURIComponent(endTime)}`,
  )
}

export function getTrajectoryCenter() {
  return request<TrajectoryCenterData>(`/vessels/center`)
}

export function getTrackStatistics(mmsi: number, startTime: string, endTime: string) {
  return request<TrackStatisticsData>(
    `/vessels/${mmsi}/statistics?start_time=${encodeURIComponent(startTime)}&end_time=${encodeURIComponent(endTime)}`,
  )
}

export function detectArea(body: {
  mmsi: number
  start_time: string
  end_time: string
  area: Record<string, unknown>
}) {
  return request<AreaDetectionResponseData>('/analysis/area-detection', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function calcDistance(mmsi1: number, mmsi2: number, time?: string) {
  let url = `/analysis/distance?mmsi1=${mmsi1}&mmsi2=${mmsi2}`
  if (time) url += `&time=${encodeURIComponent(time)}`
  return request<DistanceResponseData>(url)
}

export function predictTrajectory(mmsi: number, durationMinutes = 60) {
  return request<PredictionResponseData>(
    `/vessels/${mmsi}/prediction?duration_minutes=${durationMinutes}`,
  )
}

export function predictTrajectoryFromPoints(points: ManualTrackPoint[], durationMinutes = 60, stepSeconds = 60) {
  return request<PredictionResponseData>(`/analysis/predict-manual`, {
    method: 'POST',
    body: JSON.stringify({
      points,
      duration_minutes: durationMinutes,
      step_seconds: stepSeconds,
    }),
  })
}

export function getSimilarTracksFromPoints(points: ManualTrackPoint[], topK = 5) {
  return request<SimilarTracksResponseData>(`/analysis/similar-tracks`, {
    method: 'POST',
    body: JSON.stringify({ points, top_k: topK }),
  })
}

export function getPredictorAssetsStatus() {
  return request<PredictorAssetsStatusData>('/analysis/predictor-assets/status')
}

export function preparePredictorAssets() {
  return request<PredictorAssetsPrepareStartData>('/analysis/predictor-assets/prepare', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export function getPredictorAssetsPrepareTask(taskId: string) {
  return request<PredictorAssetsPrepareTaskData>(`/analysis/predictor-assets/tasks/${encodeURIComponent(taskId)}`)
}

/* ---- Data Import ---- */

export interface ImportResult {
  segments_inserted: number
  rows_inserted: number
  trips_rebuilt: boolean
  source?: string
}

export interface ImportTaskStartResult {
  task_id: string
  status: string
  stage: string
  progress: number
  source?: string
}

export interface ImportTaskStatus {
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  stage: string
  progress: number
  source: string
  filename: string | null
  total_rows: number
  current_rows: number
  rows_inserted: number
  segments_inserted: number
  eta_seconds: number | null
  mobility_status: 'queued' | 'running' | 'completed' | 'failed'
  mobility_stage: string
  mobility_progress: number
  mobility_total_rows: number
  mobility_current_rows: number
  mobility_eta_seconds: number | null
  pkl_status: 'queued' | 'running' | 'completed' | 'failed'
  pkl_stage: string
  pkl_progress: number
  pkl_eta_seconds: number | null
  pkl_sample_count: number
  pkl_output_path: string | null
  trips_rebuilt: boolean
  error: string | null
  created_at: string
  updated_at: string
}

export async function importAisCsv(file: File, rebuildTrips = true): Promise<ImportTaskStartResult> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`/api/data/import-csv?rebuild_trips=${rebuildTrips}`, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  const json = await res.json()
  return json.data
}

export async function importAisCsvByPath(filePath: string, rebuildTrips = true): Promise<ImportTaskStartResult> {
  const res = await fetch(`${BASE}/data/import-path`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_path: filePath, rebuild_trips: rebuildTrips }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  const json = await res.json()
  return json.data
}

export function getImportTask(taskId: string) {
  return request<ImportTaskStatus>(`/data/import-tasks/${encodeURIComponent(taskId)}`)
}

export function listImportTasks(limit = 6) {
  return request<ImportTaskStatus[]>(`/data/import-tasks?limit=${limit}`)
}

/* ---- Stop Detection ---- */

export interface StopPoint {
  startTime: string
  endTime: string
  durationMinutes: number
  lat: number
  lon: number
  pointCount: number
}

export interface StopDetectionResponse {
  mmsi: number
  stop_count: number
  total_duration_minutes: number
  stops: StopPoint[]
}

export function getStopPoints(
  mmsi: number,
  distanceThresholdM: number,
  timeThresholdMinutes: number,
  startTime?: string,
  endTime?: string,
) {
  let url = `/stops/${mmsi}?distance_threshold_m=${distanceThresholdM}&time_threshold_minutes=${timeThresholdMinutes}`
  if (startTime) url += `&start_time=${encodeURIComponent(startTime)}`
  if (endTime) url += `&end_time=${encodeURIComponent(endTime)}`
  return request<StopDetectionResponse>(url)
}

/* ---- Heatmap ---- */

export interface HeatmapPoint {
  lat: number
  lon: number
  intensity: number
}

export interface HeatmapResponse {
  points: HeatmapPoint[]
  total_points: number
  bounds: {
    min_lat: number
    max_lat: number
    min_lon: number
    max_lon: number
  }
}

export function getTrajectoryHeatmap(
  bounds: { min_lat: number; max_lat: number; min_lon: number; max_lon: number },
  startTime?: string,
  endTime?: string,
  granularity = 60,
) {
  let url = `/heatmap/trajectory?min_lat=${bounds.min_lat}&max_lat=${bounds.max_lat}&min_lon=${bounds.min_lon}&max_lon=${bounds.max_lon}&granularity=${granularity}`
  if (startTime) url += `&start_time=${encodeURIComponent(startTime)}`
  if (endTime) url += `&end_time=${encodeURIComponent(endTime)}`
  return request<HeatmapResponse>(url)
}

export function getVesselsDensity(bounds: {
  min_lat: number
  max_lat: number
  min_lon: number
  max_lon: number
}) {
  const url = `/heatmap/vessels-density?min_lat=${bounds.min_lat}&max_lat=${bounds.max_lat}&min_lon=${bounds.min_lon}&max_lon=${bounds.max_lon}`
  return request<HeatmapResponse>(url)
}

/* ---- CPA (Closest Point of Approach) ---- */

export interface CPAResult {
  mmsi_a: number
  name_a: string
  mmsi_b: number
  name_b: string
  cpa_time: string
  min_distance_m: number
  min_distance_nm: number
  safety_status: 'danger' | 'warning' | 'safe'
  safety_text: string
  position_a: { lon: number; lat: number }
  position_b: { lon: number; lat: number }
  sog_a: number
  sog_b: number
  shortest_line: {
    a: { lon: number; lat: number }
    b: { lon: number; lat: number }
  }
}

export function analyzeCPA(mmsiA: number, mmsiB: number) {
  return request<CPAResult>(`/cpa/analyze?mmsi_a=${mmsiA}&mmsi_b=${mmsiB}`)
}

/* ---- Animation ---- */

export interface AnimationFrame {
  timestamp: string
  lat: number
  lon: number
  sog: number
  cog: number
}

export interface AnimationData {
  mmsi: number
  frame_count: number
  start_time: string
  end_time: string
  step_seconds: number
  frames: AnimationFrame[]
}

export interface TrajectoryTimeRange {
  mmsi: number
  start_time: string
  end_time: string
  point_count: number
}

export function getAnimationFrames(
  mmsi: number,
  startTime: string,
  endTime: string,
  stepSeconds = 60,
) {
  const url = `/animation/${mmsi}/frames?start_time=${encodeURIComponent(startTime)}&end_time=${encodeURIComponent(endTime)}&step_seconds=${stepSeconds}`
  return request<AnimationData>(url)
}

export function getTrajectoryTimeRange(mmsi: number) {
  return request<TrajectoryTimeRange>(`/animation/${mmsi}/range`)
}
