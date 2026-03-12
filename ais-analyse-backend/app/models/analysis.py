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
    method: str = "linear_extrapolation"
