const BASE = '/api'

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
