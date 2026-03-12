export interface ShipPosition {
  lat: number
  lon: number
  sog: number
  cog: number
  heading: number
}

export interface Ship {
  mmsi: number
  vessel_name: string
  vessel_type: number
  imo: string
  call_sign: string
  length: number
  width: number
  draft: number
  status: number
  cargo: number
  position: ShipPosition
  track: [number, number][] // [lon, lat]
  color: string
}

export interface TrackStatistics {
  distance: string
  duration: string
  maxSpeed: string
  avgSpeed: string
  speedSeries: { time: string; speed: number }[]
}

export interface AreaDetectionResult {
  entered: boolean
  shipName: string
  enterTime: string | null
  exitTime: string | null
  stayDuration: string | null
  insideTrack: GeoJSON.LineString | null
}

export interface DistanceResultData {
  ship1Name: string
  ship2Name: string
  ship1Color: string
  ship2Color: string
  distance: number
  minDistance: number
  minDistanceTime: string | null
}

export interface PredictionResultData {
  shipName: string
  confidence: number
  points: number
  endPoint: [number, number]
  predictedTrack: GeoJSON.LineString
  predictedTimestamps: string[]
  method: string
}

export type ToastType = 'success' | 'warning' | 'info' | 'error'

export const VESSEL_TYPES: Record<number, string> = {
  30: '渔船',
  60: '客船',
  70: '货船',
  80: '油轮',
  0: '其他',
}

export const NAV_STATUS: Record<number, string> = {
  0: '航行中',
  1: '锚泊',
  2: '失控',
  3: '受限',
  5: '系泊',
  15: '未定义',
}
