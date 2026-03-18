from pydantic import BaseModel, Field
from datetime import datetime


class TrackStatistics(BaseModel):
    """航行统计"""
    mmsi: int
    distance_km: float
    duration_hours: float
    max_speed_knots: float
    avg_speed_knots: float
    speed_series: list[dict] = []


class AreaDetectionRequest(BaseModel):
    """区域检测请求"""
    mmsi: int = Field(..., ge=100000000, le=999999999)
    start_time: datetime
    end_time: datetime
    area: dict  # GeoJSON Polygon


class AreaDetectionResponse(BaseModel):
    """区域检测响应"""
    entered: bool
    enter_time: datetime | None = None
    exit_time: datetime | None = None
    stay_duration_minutes: float | None = None
    inside_track: dict | None = None  # GeoJSON


class DistanceResponse(BaseModel):
    """两船距离响应"""
    mmsi1: int
    mmsi2: int
    current_distance_km: float
    min_distance_km: float
    min_distance_time: datetime | None = None


class PredictionResponse(BaseModel):
    """轨迹预测响应"""
    mmsi: int
    predicted_track: dict  # GeoJSON LineString
    predicted_timestamps: list[str] = []
    confidence: float
    method: str = "mutual_attention_opt"


class ManualTrackPoint(BaseModel):
    lon: float
    lat: float


class ManualPredictionRequest(BaseModel):
    points: list[ManualTrackPoint] = Field(..., min_length=2, max_length=500)
    duration_minutes: int = Field(default=60, ge=5, le=360)
    step_seconds: int = Field(default=60, ge=5, le=300)


class SimilarTrackItem(BaseModel):
    rank: int
    global_traj_id: int
    track: dict  # GeoJSON LineString


class SimilarTracksResponse(BaseModel):
    tracks: list[SimilarTrackItem] = []


class SimilarTracksRequest(BaseModel):
    points: list[ManualTrackPoint] = Field(..., min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)
