"""
轨迹热力图 API：查询指定区域/时间段的船舶轨迹点密度
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings

router = APIRouter(prefix="/api/heatmap", tags=["heatmap"])
settings = get_settings()


class HeatmapPoint(BaseModel):
    lat: float
    lon: float
    intensity: float


class HeatmapResponse(BaseModel):
    points: List[HeatmapPoint]
    total_points: int
    bounds: dict


@router.get("/trajectory", response_model=dict)
async def get_trajectory_heatmap(
    min_lat: float = Query(..., ge=-90, le=90, description="最小纬度"),
    max_lat: float = Query(..., ge=-90, le=90, description="最大纬度"),
    min_lon: float = Query(..., ge=-180, le=180, description="最小经度"),
    max_lon: float = Query(..., ge=-180, le=180, description="最大经度"),
    start_time: datetime | None = Query(None, description="起始时间 ISO 8601"),
    end_time: datetime | None = Query(None, description="结束时间 ISO 8601"),
    granularity: int = Query(default=60, ge=10, le=80, description="采样粒度"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取指定矩形区域内的轨迹热力图数据。
    """
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=400, detail="无效的经纬度边界")
    
    # 构建时间过滤条件
    time_filter = ""
    params = {
        "min_lat": min_lat,
        "max_lat": max_lat,
        "min_lon": min_lon,
        "max_lon": max_lon,
        "granularity": granularity,
    }
    
    if start_time and end_time:
        time_filter = "AND base_date_time BETWEEN :t_start AND :t_end"
        params["t_start"] = start_time
        params["t_end"] = end_time
    
    # 使用简单的网格聚合查询（兼容所有 PostgreSQL 版本）
    query = text(f"""
        WITH raw_points AS (
            SELECT 
                latitude,
                longitude,
                FLOOR((latitude - :min_lat) / ((:max_lat - :min_lat) / :granularity)) AS grid_x,
                FLOOR((longitude - :min_lon) / ((:max_lon - :min_lon) / :granularity)) AS grid_y
            FROM ais_raw
            WHERE latitude BETWEEN :min_lat AND :max_lat
              AND longitude BETWEEN :min_lon AND :max_lon
              {time_filter}
        ),
        grid AS (
            SELECT 
                grid_x,
                grid_y,
                COUNT(*) AS point_count,
                AVG(latitude) AS avg_lat,
                AVG(longitude) AS avg_lon
            FROM raw_points
            WHERE grid_x >= 0 AND grid_x < :granularity
              AND grid_y >= 0 AND grid_y < :granularity
            GROUP BY grid_x, grid_y
            HAVING COUNT(*) > 0
        )
        SELECT 
            avg_lat AS lat,
            avg_lon AS lon,
            -- 使用对数缩放让热力分布更均匀，低强度区域也能显示
            LEAST(LN(point_count::float + 1.0) / NULLIF(LN((SELECT MAX(point_count) FROM grid) + 1.0), 0), 1.0) AS intensity
        FROM grid
        ORDER BY point_count DESC
        LIMIT 2000
    """)
    
    try:
        result = await db.execute(query, params)
        rows = result.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")
    
    points = [
        HeatmapPoint(
            lat=float(row.lat),
            lon=float(row.lon),
            intensity=round(float(row.intensity), 2)
        )
        for row in rows if row.lat is not None and row.lon is not None
    ]
    
    return {
        "code": 200,
        "data": {
            "points": [p.model_dump() for p in points],
            "total_points": len(points),
            "bounds": {
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lon": min_lon,
                "max_lon": max_lon,
            }
        }
    }


@router.get("/vessels-density", response_model=dict)
async def get_vessels_density(
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
):
    """
    获取区域内船舶数量密度（按 MMSI 去重）。
    """
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=400, detail="无效的经纬度边界")
    
    granularity = 30  # 固定粒度
    
    query = text("""
        WITH vessel_grid AS (
            SELECT
                mmsi,
                FLOOR((latitude - :min_lat) / ((:max_lat - :min_lat) / :granularity)) AS grid_x,
                FLOOR((longitude - :min_lon) / ((:max_lon - :min_lon) / :granularity)) AS grid_y,
                AVG(latitude) AS avg_lat,
                AVG(longitude) AS avg_lon
            FROM ais_raw
            WHERE latitude BETWEEN :min_lat AND :max_lat
              AND longitude BETWEEN :min_lon AND :max_lon
            GROUP BY mmsi, grid_x, grid_y
        ),
        density AS (
            SELECT
                grid_x,
                grid_y,
                AVG(avg_lat) AS lat,
                AVG(avg_lon) AS lon,
                COUNT(DISTINCT mmsi) AS vessel_count
            FROM vessel_grid
            WHERE grid_x >= 0 AND grid_x < :granularity
              AND grid_y >= 0 AND grid_y < :granularity
            GROUP BY grid_x, grid_y
        )
        SELECT
            lat,
            lon,
            LEAST(vessel_count::float / NULLIF((SELECT MAX(vessel_count) FROM density), 0), 1.0) AS intensity
        FROM density
        ORDER BY vessel_count DESC
        LIMIT 500
    """)
    
    try:
        result = await db.execute(query, {
            "min_lat": min_lat,
            "max_lat": max_lat,
            "min_lon": min_lon,
            "max_lon": max_lon,
            "granularity": granularity,
        })
        rows = result.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")
    
    points = [
        HeatmapPoint(
            lat=float(row.lat),
            lon=float(row.lon),
            intensity=round(float(row.intensity), 2)
        )
        for row in rows if row.lat is not None and row.lon is not None
    ]
    
    return {
        "code": 200,
        "data": {
            "points": [p.model_dump() for p in points],
            "total_points": len(points),
            "type": "vessels_density"
        }
    }
