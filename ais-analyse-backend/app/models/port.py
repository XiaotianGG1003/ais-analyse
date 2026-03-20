from datetime import datetime

from pydantic import BaseModel, Field


class PortBBox(BaseModel):
    min_lon: float = Field(..., ge=-180, le=180)
    min_lat: float = Field(..., ge=-90, le=90)
    max_lon: float = Field(..., ge=-180, le=180)
    max_lat: float = Field(..., ge=-90, le=90)


class PortCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    bbox: PortBBox


class PortItem(BaseModel):
    id: int
    name: str
    bbox: PortBBox
    polygon: dict
    created_at: datetime


class PortListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PortItem]


class PortStayVessel(BaseModel):
    mmsi: int
    vessel_name: str | None = None
    stay_minutes: float
    visit_count: int


class PortAnalysisResponse(BaseModel):
    port_id: int
    port_name: str
    start_time: datetime
    end_time: datetime
    unique_vessel_count: int
    entry_count: int
    exit_count: int
    total_stay_minutes: float
    avg_stay_minutes: float
    top_vessels: list[PortStayVessel]
