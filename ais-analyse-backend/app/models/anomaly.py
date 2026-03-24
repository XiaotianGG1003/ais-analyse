from datetime import datetime

from pydantic import BaseModel, Field


class ForbiddenAreaItem(BaseModel):
    name: str | None = None
    geometry: dict


class AnomalyDetectionRequest(BaseModel):
    mmsi: int = Field(..., ge=100000000, le=999999999)
    start_time: datetime
    end_time: datetime
    speed_threshold_knots: float = Field(default=30.0, ge=5.0, le=60.0)
    turn_rate_threshold_deg_per_min: float = Field(default=20.0, ge=5.0, le=120.0)
    stop_speed_threshold_knots: float = Field(default=1.0, ge=0.1, le=5.0)
    stop_min_minutes: int = Field(default=30, ge=5, le=240)
    stop_radius_m: float = Field(default=500.0, ge=50.0, le=5000.0)
    forbidden_area: dict | None = None  # 兼容旧字段：GeoJSON Polygon
    forbidden_areas: list[dict | ForbiddenAreaItem] | None = None


class AnomalyEvent(BaseModel):
    event_id: str
    event_type: str
    severity: str
    score: float
    start_time: str
    end_time: str
    position: dict
    forbidden_area_name: str | None = None
    evidence: dict


class AnomalyDetectionResponse(BaseModel):
    mmsi: int
    start_time: str
    end_time: str
    event_count: int
    severity_count: dict[str, int] = Field(default_factory=dict)
    type_count: dict[str, int] = Field(default_factory=dict)
    events: list[AnomalyEvent] = Field(default_factory=list)
