from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.models.analysis import (
    AreaDetectionRequest,
    AreaDetectionResponse,
    DistanceResponse,
    PredictionResponse,
    TrackStatistics,
)
from app.services import analysis_service

router = APIRouter(prefix="/api", tags=["analysis"])
settings = get_settings()


@router.get("/vessels/{mmsi}/statistics", response_model=dict)
async def get_statistics(
    mmsi: int,
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """航行统计"""
    if mmsi < 100000000 or mmsi > 999999999:
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    delta = (end_time - start_time).days
    if delta > settings.max_query_days:
        raise HTTPException(
            status_code=400,
            detail=f"查询时间跨度不能超过 {settings.max_query_days} 天",
        )
    stats = await analysis_service.get_track_statistics(db, mmsi, start_time, end_time)
    if not stats:
        raise HTTPException(status_code=404, detail="未找到统计数据")
    return {"code": 200, "data": stats.model_dump()}


@router.post("/analysis/area-detection", response_model=dict)
async def area_detection(
    req: AreaDetectionRequest,
    db: AsyncSession = Depends(get_db),
):
    """区域检测 — 判断船舶是否进入指定区域"""
    delta = (req.end_time - req.start_time).days
    if delta > settings.max_query_days:
        raise HTTPException(
            status_code=400,
            detail=f"查询时间跨度不能超过 {settings.max_query_days} 天",
        )
    if req.area.get("type") != "Polygon":
        raise HTTPException(status_code=400, detail="区域必须是 GeoJSON Polygon 类型")
    result = await analysis_service.detect_area(db, req)
    return {"code": 200, "data": result.model_dump()}


@router.get("/analysis/distance", response_model=dict)
async def calc_distance(
    mmsi1: int = Query(..., ge=100000000, le=999999999),
    mmsi2: int = Query(..., ge=100000000, le=999999999),
    time: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """两船距离计算"""
    if mmsi1 == mmsi2:
        raise HTTPException(status_code=400, detail="两个 MMSI 不能相同")
    result = await analysis_service.calc_distance(db, mmsi1, mmsi2, time)
    if not result:
        raise HTTPException(status_code=404, detail="未找到船舶数据")
    return {"code": 200, "data": result.model_dump()}


@router.get("/vessels/{mmsi}/prediction", response_model=dict)
async def predict_trajectory(
    mmsi: int,
    duration_minutes: int = Query(
        default=60, ge=5, le=360, description="预测时长（分钟）"
    ),
    db: AsyncSession = Depends(get_db),
):
    """轨迹预测"""
    if mmsi < 100000000 or mmsi > 999999999:
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    result = await analysis_service.predict_trajectory(
        db,
        mmsi,
        duration_minutes,
        settings.prediction_step_minutes,
    )
    if not result:
        raise HTTPException(status_code=404, detail="轨迹点不足，无法预测")
    return {"code": 200, "data": result.model_dump()}
