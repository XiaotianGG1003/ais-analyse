from pydantic import BaseModel, Field
from datetime import datetime


class VesselBrief(BaseModel):
    """船舶简要信息（搜索/列表用）"""
    mmsi: int
    vessel_name: str | None = None
    vessel_type: int | None = None
    length: float | None = None
    width: float | None = None


class LastPosition(BaseModel):
    """最新位置"""
    longitude: float
    latitude: float
    timestamp: datetime | None = None
    sog: float | None = None
    cog: float | None = None


class VesselDetail(VesselBrief):
    """船舶详细信息"""
    imo: str | None = None
    call_sign: str | None = None
    status: int | None = None
    draft: float | None = None
    last_position: LastPosition | None = None


class VesselListItem(VesselBrief):
    """船舶列表项"""
    last_time: datetime | None = None


class VesselListResponse(BaseModel):
    """分页船舶列表"""
    total: int
    page: int
    page_size: int
    items: list[VesselListItem]


class TrackPoint(BaseModel):
    """轨迹点"""
    longitude: float
    latitude: float
    timestamp: datetime | None = None
    sog: float | None = None
    cog: float | None = None


class TrackResponse(BaseModel):
    """轨迹查询响应"""
    mmsi: int
    vessel_name: str | None = None
    track: dict  # GeoJSON LineString
    timestamps: list[str] = []
    point_count: int = 0
