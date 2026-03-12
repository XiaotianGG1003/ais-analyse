from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.models.vessel import VesselBrief, VesselDetail, VesselListResponse, TrackResponse
from app.services import vessel_service

router = APIRouter(prefix="/api/vessels", tags=["vessels"])
settings = get_settings()


@router.get("/search", response_model=dict)
async def search_vessels(
    keyword: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """船舶搜索 — 按 MMSI 或船名关键字搜索"""
    vessels = await vessel_service.search_vessels(db, keyword, limit)
    return {"code": 200, "data": [v.model_dump() for v in vessels]}


@router.get("", response_model=dict)
async def list_vessels(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    vessel_type: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """船舶列表 — 分页获取"""
    page_size = min(page_size, settings.max_page_size)
    result = await vessel_service.list_vessels(db, page, page_size, vessel_type)
    return {"code": 200, "data": result.model_dump()}


@router.get("/{mmsi}", response_model=dict)
async def get_vessel_detail(
    mmsi: int,
    db: AsyncSession = Depends(get_db),
):
    """船舶详情"""
    if mmsi < 100000000 or mmsi > 999999999:
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    vessel = await vessel_service.get_vessel_detail(db, mmsi)
    if not vessel:
        raise HTTPException(status_code=404, detail="船舶不存在")
    return {"code": 200, "data": vessel.model_dump()}


@router.get("/{mmsi}/track", response_model=dict)
async def get_vessel_track(
    mmsi: int,
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """轨迹查询"""
    if mmsi < 100000000 or mmsi > 999999999:
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    delta = (end_time - start_time).days
    if delta > settings.max_query_days:
        raise HTTPException(
            status_code=400,
            detail=f"查询时间跨度不能超过 {settings.max_query_days} 天",
        )
    track = await vessel_service.get_vessel_track(db, mmsi, start_time, end_time)
    if not track:
        raise HTTPException(status_code=404, detail="未找到该时间段的轨迹数据")
    return {"code": 200, "data": track.model_dump()}
