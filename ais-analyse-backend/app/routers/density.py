"""
轨迹时空聚合与密度分析模块
使用 MobilityDB 的 tgeompoint 和 trajectory 函数
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import Optional
import math

from app.database import get_db

router = APIRouter(prefix="/api/density", tags=["density"])


@router.get("/heatmap", response_model=dict)
async def get_density_heatmap(
    start_time: Optional[str] = Query(None, description="开始时间 (ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间 (ISO格式)"),
    grid_size: float = Query(0.01, description="空间网格大小（度）"),
    time_interval: str = Query("1 hour", description="时间间隔（如: 30 min, 1 hour）"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取轨迹时空热力图数据
    使用 ST_SnapToGrid 进行空间聚合，配合时间窗口
    """
    # 检查 vessels 表
    check_table = text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'vessels'
        )
    """)
    result = await db.execute(check_table)
    if not result.scalar():
        raise HTTPException(status_code=500, detail="vessels 表不存在")
    
    # 构建时间过滤条件
    time_filter = ""
    params = {"grid_size": grid_size}
    
    if start_time and end_time:
        time_filter = """
            AND startTimestamp(trip) <= :end_time 
            AND endTimestamp(trip) >= :start_time
        """
        params["start_time"] = end_time  # trip结束要大于start_time
        params["end_time"] = start_time  # trip开始要小于end_time
    
    query = text(f"""
        WITH limited_vessels AS (
            -- 限制处理船舶数量，优先处理时间范围最近的
            SELECT mmsi, vessel_name, trip
            FROM vessels
            WHERE trip IS NOT NULL
            {time_filter}
            ORDER BY endTimestamp(trip) DESC
            LIMIT 1000
        ),
        trajectory_points AS (
            -- 将轨迹展开为点序列
            SELECT 
                mmsi,
                vessel_name,
                unnest(instants(trip)) as instant
            FROM limited_vessels
        ),
        point_data AS (
            -- 提取坐标和时间
            SELECT 
                mmsi,
                vessel_name,
                getTimestamp(instant) as timestamp,
                ST_X(getValue(instant)::geometry) as lon,
                ST_Y(getValue(instant)::geometry) as lat
            FROM trajectory_points
        ),
        space_grid AS (
            -- 空间网格聚合
            SELECT 
                ST_SnapToGrid(ST_MakePoint(lon, lat), :grid_size) as grid_point,
                date_trunc('hour', timestamp) as time_bucket,
                COUNT(*) as point_count,
                COUNT(DISTINCT mmsi) as vessel_count,
                AVG(lon) as avg_lon,
                AVG(lat) as avg_lat
            FROM point_data
            GROUP BY 
                ST_SnapToGrid(ST_MakePoint(lon, lat), :grid_size),
                date_trunc('hour', timestamp)
        )
        SELECT 
            ST_X(grid_point) as grid_lon,
            ST_Y(grid_point) as grid_lat,
            time_bucket,
            point_count,
            vessel_count,
            avg_lon,
            avg_lat
        FROM space_grid
        WHERE point_count > 1  -- 过滤孤立点
        ORDER BY point_count DESC
        LIMIT 1000
    """)
    
    try:
        result = await db.execute(query, params)
        rows = result.fetchall()
        
        heatmap_data = []
        for row in rows:
            heatmap_data.append({
                "lon": row.grid_lon,
                "lat": row.grid_lat,
                "time_bucket": row.time_bucket.isoformat() if hasattr(row.time_bucket, 'isoformat') else str(row.time_bucket),
                "point_count": row.point_count,
                "vessel_count": row.vessel_count,
                "intensity": min(1.0, row.point_count / 100.0),  # 归一化强度
            })
        
        return {
            "code": 200,
            "data": {
                "grid_size": grid_size,
                "time_interval": time_interval,
                "total_cells": len(heatmap_data),
                "heatmap": heatmap_data,
            }
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"热力图计算失败: {str(e)}")


@router.get("/corridors", response_model=dict)
async def get_busy_corridors(
    start_time: Optional[str] = Query(None, description="开始时间 (ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间 (ISO格式)"),
    min_vessels: int = Query(5, description="最小船舶数量阈值"),
    grid_size: float = Query(0.005, description="空间网格大小（度，约500米）"),
    db: AsyncSession = Depends(get_db),
):
    """
    识别繁忙航道
    基于轨迹密度和船舶数量识别主要航道
    """
    # 构建时间过滤条件
    time_filter = ""
    params = {"grid_size": grid_size, "min_vessels": min_vessels}
    
    if start_time and end_time:
        time_filter = """
            AND startTimestamp(trip) <= :end_time 
            AND endTimestamp(trip) >= :start_time
        """
        params["start_time"] = end_time  # trip结束要大于start_time
        params["end_time"] = start_time  # trip开始要小于end_time
    
    query = text(f"""
        WITH limited_vessels AS (
            SELECT mmsi, vessel_name, trip
            FROM vessels
            WHERE trip IS NOT NULL
            {time_filter}
            ORDER BY endTimestamp(trip) DESC
            LIMIT 500
        ),
        trajectory_segments AS (
            -- 将轨迹转换为线段
            SELECT 
                mmsi,
                vessel_name,
                unnest(segments(trip)) as segment
            FROM limited_vessels
        ),
        segment_points AS (
            -- 提取线段的起点和终点
            SELECT 
                mmsi,
                vessel_name,
                ST_X(startValue(segment)::geometry) as start_lon,
                ST_Y(startValue(segment)::geometry) as start_lat,
                ST_X(endValue(segment)::geometry) as end_lon,
                ST_Y(endValue(segment)::geometry) as end_lat,
                startTimestamp(segment) as start_time,
                endTimestamp(segment) as end_time
            FROM trajectory_segments
        ),
        grid_corridors AS (
            -- 网格化并统计
            SELECT 
                ST_SnapToGrid(ST_MakePoint(start_lon, start_lat), :grid_size) as grid_start,
                ST_SnapToGrid(ST_MakePoint(end_lon, end_lat), :grid_size) as grid_end,
                COUNT(*) as passage_count,
                COUNT(DISTINCT mmsi) as unique_vessels,
                AVG(sqrt(
                    POW(end_lon - start_lon, 2) + 
                    POW(end_lat - start_lat, 2)
                ) * 111000) as avg_segment_length_m,
                AVG(EXTRACT(EPOCH FROM (end_time - start_time))) as avg_duration_sec
            FROM segment_points
            WHERE ABS(end_lon - start_lon) > 0.0001 
               OR ABS(end_lat - start_lat) > 0.0001  -- 排除静止点
            GROUP BY 
                ST_SnapToGrid(ST_MakePoint(start_lon, start_lat), :grid_size),
                ST_SnapToGrid(ST_MakePoint(end_lon, end_lat), :grid_size)
            HAVING COUNT(DISTINCT mmsi) >= :min_vessels
        )
        SELECT 
            ST_X(grid_start) as start_lon,
            ST_Y(grid_start) as start_lat,
            ST_X(grid_end) as end_lon,
            ST_Y(grid_end) as end_lat,
            passage_count,
            unique_vessels,
            avg_segment_length_m,
            avg_duration_sec,
            -- 计算方向角
            DEGREES(ST_Azimuth(grid_start, grid_end)) as direction
        FROM grid_corridors
        ORDER BY unique_vessels DESC, passage_count DESC
        LIMIT 500
    """)
    
    try:
        result = await db.execute(query, {
            "start_time": start_time,
            "end_time": end_time,
            "min_vessels": min_vessels,
            "grid_size": grid_size
        })
        rows = result.fetchall()
        
        corridors = []
        for row in rows:
            # 计算平均速度（节）
            avg_speed_knots = 0
            avg_duration_sec = float(row.avg_duration_sec) if row.avg_duration_sec else 0
            avg_segment_length_m = float(row.avg_segment_length_m) if row.avg_segment_length_m else 0
            if avg_duration_sec > 0:
                avg_speed_knots = (avg_segment_length_m / avg_duration_sec) * 1.94384
            
            corridors.append({
                "start": {"lon": row.start_lon, "lat": row.start_lat},
                "end": {"lon": row.end_lon, "lat": row.end_lat},
                "passage_count": row.passage_count,
                "unique_vessels": row.unique_vessels,
                "avg_speed_knots": round(avg_speed_knots, 2),
                "direction": round(row.direction, 2) if row.direction else None,
                "intensity": min(1.0, row.unique_vessels / 50.0),
            })
        
        return {
            "code": 200,
            "data": {
                "total_corridors": len(corridors),
                "min_vessels_threshold": min_vessels,
                "corridors": corridors,
            }
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"航道分析失败: {str(e)}")


@router.get("/speed-analysis", response_model=dict)
async def get_speed_analysis(
    start_time: Optional[str] = Query(None, description="开始时间 (ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间 (ISO格式)"),
    grid_size: float = Query(0.01, description="空间网格大小（度）"),
    db: AsyncSession = Depends(get_db),
):
    """
    速度时空分析
    使用 twavg(speed(Trip)) 计算时权平均速度
    """
    # 构建时间过滤条件
    time_filter = ""
    params = {"grid_size": grid_size}
    
    if start_time and end_time:
        time_filter = """
            AND startTimestamp(trip) <= :end_time 
            AND endTimestamp(trip) >= :start_time
        """
        params["start_time"] = end_time
        params["end_time"] = start_time
    
    query = text(f"""
        WITH limited_vessels AS (
            SELECT mmsi, vessel_name, trip
            FROM vessels
            WHERE trip IS NOT NULL
            {time_filter}
            ORDER BY endTimestamp(trip) DESC
            LIMIT 1000
        ),
        vessel_speeds AS (
            SELECT 
                mmsi,
                vessel_name,
                -- 计算时权平均速度 (m/s)
                twavg(speed(trip)) as avg_speed_ms,
                -- 轨迹中心点
                ST_Centroid(ST_Collect(valueAtTimestamp(trip, startTimestamp(trip))::geometry, 
                                       valueAtTimestamp(trip, endTimestamp(trip))::geometry)) as centroid
            FROM limited_vessels
        ),
        speed_grid AS (
            SELECT 
                ST_SnapToGrid(centroid, 0.01) as grid_point,
                AVG(avg_speed_ms) as avg_speed_ms,
                COUNT(*) as vessel_count,
                STDDEV(avg_speed_ms) as speed_stddev
            FROM vessel_speeds
            WHERE avg_speed_ms IS NOT NULL
            GROUP BY ST_SnapToGrid(centroid, 0.01)
        )
        SELECT 
            ST_X(grid_point) as lon,
            ST_Y(grid_point) as lat,
            avg_speed_ms,
            vessel_count,
            speed_stddev
        FROM speed_grid
        WHERE vessel_count >= 2
        ORDER BY avg_speed_ms DESC
        LIMIT 500
    """)
    
    try:
        result = await db.execute(query, {
            "start_time": start_time,
            "end_time": end_time,
            "grid_size": grid_size
        })
        rows = result.fetchall()
        
        speed_data = []
        for row in rows:
            # 转换 m/s 到 knots
            speed_knots = (float(row.avg_speed_ms) * 1.94384) if row.avg_speed_ms else 0
            speed_stddev = float(row.speed_stddev) if row.speed_stddev else 0
            
            speed_data.append({
                "lon": row.lon,
                "lat": row.lat,
                "avg_speed_knots": round(speed_knots, 2),
                "vessel_count": row.vessel_count,
                "speed_variance": round(speed_stddev * 1.94384, 2),
            })
        
        return {
            "code": 200,
            "data": {
                "total_cells": len(speed_data),
                "speed_data": speed_data,
            }
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"速度分析失败: {str(e)}")


@router.get("/time-distribution", response_model=dict)
async def get_time_distribution(
    mmsi: Optional[int] = Query(None, description="指定船舶MMSI，不指定则统计全部"),
    time_bucket: str = Query("1 hour", description="时间桶大小（如: 1 hour, 30 min）"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取船舶活动时间分布
    统计不同时段的船舶活动密度
    """
    mmsi_filter = ""
    params = {"time_bucket": time_bucket}
    
    if mmsi:
        mmsi_filter = "AND mmsi = :mmsi"
        params["mmsi"] = mmsi
    
    query = text(f"""
        WITH time_buckets AS (
            SELECT 
                mmsi,
                vessel_name,
                -- 展开轨迹时间点
                unnest(timestamps(trip)) as ts
            FROM vessels
            WHERE trip IS NOT NULL {mmsi_filter}
        )
        SELECT 
            date_trunc(:time_bucket, ts) as time_slot,
            COUNT(*) as point_count,
            COUNT(DISTINCT mmsi) as vessel_count
        FROM time_buckets
        GROUP BY date_trunc(:time_bucket, ts)
        ORDER BY time_slot
    """)
    
    try:
        result = await db.execute(query, params)
        rows = result.fetchall()
        
        distribution = []
        for row in rows:
            distribution.append({
                "time_slot": row.time_slot.isoformat() if hasattr(row.time_slot, 'isoformat') else str(row.time_slot),
                "point_count": row.point_count,
                "vessel_count": row.vessel_count,
            })
        
        return {
            "code": 200,
            "data": {
                "time_bucket": time_bucket,
                "total_slots": len(distribution),
                "distribution": distribution,
            }
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"时间分布统计失败: {str(e)}")
