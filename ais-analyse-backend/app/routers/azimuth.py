"""
航向/方位角分析模块
使用 MobilityDB 的 azimuth() 和 bearing() 函数分析船舶航向变化
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

from app.database import get_db

router = APIRouter(prefix="/api/azimuth", tags=["azimuth"])


class HeadingDataPoint(BaseModel):
    timestamp: str
    heading: float  # 航向角度 (0-360°)
    turn_rate: Optional[float]  # 转向速率 (度/分钟)


class TurnEvent(BaseModel):
    timestamp: str
    heading_before: float
    heading_after: float
    turn_angle: float
    turn_rate: float
    lat: float
    lon: float


class AzimuthAnalysisResponse(BaseModel):
    mmsi: int
    vessel_name: Optional[str]
    start_time: str
    end_time: str
    point_count: int
    min_heading: float
    max_heading: float
    avg_heading: float
    heading_std: float  # 航向标准差，表示航向稳定性
    total_turn_angle: float  # 总转向角度
    turn_events: List[TurnEvent]
    heading_series: List[HeadingDataPoint]


class RelativeBearingResponse(BaseModel):
    mmsi_a: int
    vessel_name_a: Optional[str]
    mmsi_b: int
    vessel_name_b: Optional[str]
    bearing_series: List[dict]
    avg_bearing: float
    min_bearing: float
    max_bearing: float


@router.get("/analyze/{mmsi}", response_model=AzimuthAnalysisResponse)
async def analyze_azimuth(
    mmsi: int,
    start_time: Optional[str] = Query(None, description="开始时间 (ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间 (ISO格式)"),
    turn_threshold: float = Query(5.0, description="转向检测阈值 (度/分钟)"),
    db: AsyncSession = Depends(get_db),
):
    """
    分析单船航向变化
    使用 MobilityDB 的 azimuth() 函数获取时态方位角
    """
    # 首先检查船舶是否存在
    check_sql = text("""
        SELECT mmsi, vessel_name, trip, numInstants(trip) as point_count,
               startTimestamp(trip) as start_time, endTimestamp(trip) as end_time
        FROM vessels
        WHERE mmsi = :mmsi AND trip IS NOT NULL
    """)
    result = await db.execute(check_sql, {"mmsi": mmsi})
    vessel = result.fetchone()
    
    if not vessel:
        raise HTTPException(status_code=404, detail=f"未找到 MMSI 为 {mmsi} 的船舶或该船舶没有轨迹数据")
    
    if vessel.point_count < 2:
        raise HTTPException(status_code=400, detail="该船舶轨迹点数不足，无法计算航向")
    
    # 提取航向时间序列
    # 使用 azimuth() 获取时态方位角，degrees() 转换为角度
    time_filter_clause = ""
    params: dict = {"mmsi": mmsi}
    if start_time and end_time:
        time_filter_clause = " AND getTimestamp(inst) BETWEEN :start_time AND :end_time"
        # 将 ISO 字符串转换为 datetime 对象
        params["start_time"] = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        params["end_time"] = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
    
    heading_sql = text(f"""
        WITH vessel_trip AS (
            SELECT trip FROM vessels WHERE mmsi = :mmsi AND trip IS NOT NULL
        ),
        heading_instants AS (
            SELECT 
                getTimestamp(inst) as timestamp,
                degrees(getValue(inst)) as heading
            FROM unnest(instants(azimuth((SELECT trip FROM vessel_trip)))) as inst
            WHERE 1=1
                {time_filter_clause}
        )
        SELECT 
            timestamp,
            heading,
            heading - lag(heading) OVER (ORDER BY timestamp) as heading_diff,
            EXTRACT(EPOCH FROM (timestamp - lag(timestamp) OVER (ORDER BY timestamp))) / 60.0 as time_diff_minutes
        FROM heading_instants
        ORDER BY timestamp
    """)
    
    result = await db.execute(heading_sql, params)
    rows = result.fetchall()
    
    # 如果 MobilityDB azimuth() 返回空，尝试从原始 AIS 数据获取 COG
    if not rows or len(rows) < 2:
        try:
            cog_sql = text("""
                SELECT 
                    timestamp,
                    cog as heading,
                    cog - lag(cog) OVER (ORDER BY timestamp) as heading_diff,
                    EXTRACT(EPOCH FROM (timestamp - lag(timestamp) OVER (ORDER BY timestamp))) / 60.0 as time_diff_minutes
                FROM ais_raw
                WHERE mmsi = :mmsi 
                  AND cog IS NOT NULL
                  AND timestamp BETWEEN :start_time AND :end_time
                ORDER BY timestamp
            """)
            result = await db.execute(cog_sql, params)
            rows = result.fetchall()
        except Exception:
            rows = []
    
    if not rows or len(rows) < 2:
        # 提供更多调试信息
        vessel_start = vessel.start_time
        vessel_end = vessel.end_time
        
        if start_time and end_time:
            req_start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            req_end = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            
            if req_start > vessel_end or req_end < vessel_start:
                raise HTTPException(
                    status_code=400, 
                    detail=f"请求的时间范围 ({req_start} 到 {req_end}) 与船舶轨迹时间范围 ({vessel_start} 到 {vessel_end}) 无交集"
                )
        
        raise HTTPException(
            status_code=400, 
            detail=f"无法计算航向数据。船舶轨迹时间范围: {vessel_start} 到 {vessel_end}，轨迹点数: {vessel.point_count}。请尝试扩大时间范围或检查该时间段是否有数据。"
        )
    
    # 处理航向数据，检测转向事件
    heading_series = []
    turn_events = []
    headings = []
    total_turn = 0.0
    
    prev_row = None
    for row in rows:
        timestamp, heading, heading_diff, time_diff = row
        
        if heading is None:
            continue
            
        headings.append(heading)
        
        # 计算转向速率
        turn_rate = None
        if prev_row is not None and time_diff and time_diff > 0:
            # 处理航向跳变（如从 350° 到 10°）
            diff = heading_diff if heading_diff is not None else 0
            if diff > 180:
                diff -= 360
            elif diff < -180:
                diff += 360
            turn_rate = diff / float(time_diff) if time_diff and float(time_diff) > 0 else 0
            total_turn += abs(diff)
        
        heading_series.append({
            "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
            "heading": round(heading, 2),
            "turn_rate": round(turn_rate, 2) if turn_rate is not None else None
        })
        
        # 检测转向事件
        if prev_row is not None and turn_rate is not None and abs(turn_rate) > turn_threshold:
            prev_ts, prev_heading, _, _ = prev_row
            turn_events.append({
                "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
                "heading_before": round(prev_heading, 2),
                "heading_after": round(heading, 2),
                "turn_angle": round(heading - prev_heading if abs(heading - prev_heading) <= 180 else 
                                  (heading - prev_heading - 360 if heading > prev_heading else heading - prev_heading + 360), 2),
                "turn_rate": round(turn_rate, 2),
                "lat": 0,  # 将通过后续查询补充
                "lon": 0
            })
        
        prev_row = row
    
    # 为转向事件补充位置信息
    if turn_events:
        # 获取这些时间点的位置
        # 将 ISO 字符串转换为 datetime 对象
        turn_timestamps = [
            datetime.fromisoformat(e["timestamp"].replace('Z', '+00:00')) 
            for e in turn_events
        ]
        position_sql = text("""
            SELECT 
                getTimestamp(inst) as timestamp,
                ST_Y(getValue(inst)::geometry) as lat,
                ST_X(getValue(inst)::geometry) as lon
            FROM vessels,
                 unnest(instants(trip)) as inst
            WHERE mmsi = :mmsi 
              AND getTimestamp(inst) = ANY(:timestamps)
        """)
        pos_result = await db.execute(position_sql, {
            "mmsi": mmsi,
            "timestamps": turn_timestamps
        })
        positions = {str(r[0]): (r[1], r[2]) for r in pos_result.fetchall()}
        
        for event in turn_events:
            ts = event["timestamp"]
            if ts in positions:
                event["lat"] = positions[ts][0]
                event["lon"] = positions[ts][1]
    
    # 计算统计值
    import statistics
    avg_heading = statistics.mean(headings) if headings else 0
    heading_std = statistics.stdev(headings) if len(headings) > 1 else 0
    
    return AzimuthAnalysisResponse(
        mmsi=mmsi,
        vessel_name=vessel.vessel_name,
        start_time=str(vessel.start_time),
        end_time=str(vessel.end_time),
        point_count=len(heading_series),
        min_heading=min(headings) if headings else 0,
        max_heading=max(headings) if headings else 0,
        avg_heading=round(avg_heading, 2),
        heading_std=round(heading_std, 2),
        total_turn_angle=round(total_turn, 2),
        turn_events=turn_events,
        heading_series=heading_series
    )


@router.get("/relative-bearing", response_model=RelativeBearingResponse)
async def get_relative_bearing(
    mmsi_a: int = Query(..., description="船舶A的MMSI"),
    mmsi_b: int = Query(..., description="船舶B的MMSI"),
    start_time: Optional[str] = Query(None, description="开始时间 (ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间 (ISO格式)"),
    db: AsyncSession = Depends(get_db),
):
    """
    计算两船之间的相对方位角
    使用 MobilityDB 的 bearing() 函数
    """
    # 检查两艘船舶是否存在
    check_sql = text("""
        SELECT mmsi, vessel_name, trip
        FROM vessels
        WHERE mmsi IN (:mmsi_a, :mmsi_b) AND trip IS NOT NULL
    """)
    result = await db.execute(check_sql, {"mmsi_a": mmsi_a, "mmsi_b": mmsi_b})
    vessels = result.fetchall()
    
    if len(vessels) < 2:
        raise HTTPException(status_code=404, detail="未找到指定的船舶或船舶没有轨迹数据")
    
    vessel_map = {v[0]: {"name": v[1], "trip": v[2]} for v in vessels}
    
    # 计算相对方位
    # bearing(tpoint, tpoint) 返回从第一个点到第二个点的方位角
    time_filter_clause = ""
    params: dict = {"mmsi_a": mmsi_a, "mmsi_b": mmsi_b}
    if start_time and end_time:
        time_filter_clause = " AND getTimestamp(inst) BETWEEN :start_time AND :end_time"
        params["start_time"] = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        params["end_time"] = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
    
    bearing_sql = text(f"""
        WITH vessel_a AS (
            SELECT trip FROM vessels WHERE mmsi = :mmsi_a
        ),
        vessel_b AS (
            SELECT trip FROM vessels WHERE mmsi = :mmsi_b
        ),
        bearing_instants AS (
            SELECT 
                getTimestamp(inst) as timestamp,
                degrees(getValue(inst)) as bearing
            FROM unnest(instants(bearing(
                (SELECT trip FROM vessel_a), 
                (SELECT trip FROM vessel_b)
            ))) as inst
            WHERE 1=1
                {time_filter_clause}
        )
        SELECT timestamp, bearing
        FROM bearing_instants
        WHERE bearing IS NOT NULL
        ORDER BY timestamp
    """)
    
    result = await db.execute(bearing_sql, params)
    rows = result.fetchall()
    
    if not rows:
        raise HTTPException(status_code=400, detail="无法计算相对方位，两船时间范围可能没有重叠")
    
    bearing_series = [
        {
            "timestamp": r[0].isoformat() if isinstance(r[0], datetime) else str(r[0]),
            "bearing": round(r[1], 2) if r[1] else None
        }
        for r in rows if r[1] is not None
    ]
    
    bearings = [b["bearing"] for b in bearing_series if b["bearing"] is not None]
    
    return RelativeBearingResponse(
        mmsi_a=mmsi_a,
        vessel_name_a=vessel_map.get(mmsi_a, {}).get("name"),
        mmsi_b=mmsi_b,
        vessel_name_b=vessel_map.get(mmsi_b, {}).get("name"),
        bearing_series=bearing_series,
        avg_bearing=round(sum(bearings) / len(bearings), 2) if bearings else 0,
        min_bearing=min(bearings) if bearings else 0,
        max_bearing=max(bearings) if bearings else 0
    )


@router.get("/vessels/heading-distribution")
async def get_heading_distribution(
    start_time: Optional[str] = Query(None, description="开始时间 (ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间 (ISO格式)"),
    grid_size: float = Query(0.05, description="空间网格大小（度）"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取区域航向分布统计
    用于热力图显示不同区域的平均航向
    """
    time_filter = ""
    params: dict = {"grid_size": grid_size}
    
    if start_time and end_time:
        time_filter = "AND startTimestamp(trip) <= :end_time AND endTimestamp(trip) >= :start_time"
        params["start_time"] = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        params["end_time"] = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
    
    query = text(f"""
        WITH trajectory_points AS (
            SELECT 
                mmsi,
                unnest(instants(azimuth(trip))) as heading_instant,
                unnest(instants(trip)) as pos_instant
            FROM vessels
            WHERE trip IS NOT NULL
            {time_filter}
            LIMIT 10000
        ),
        point_data AS (
            SELECT 
                mmsi,
                getTimestamp(heading_instant) as timestamp,
                degrees(getValue(heading_instant)) as heading,
                ST_Y(getValue(pos_instant)::geometry) as lat,
                ST_X(getValue(pos_instant)::geometry) as lon
            FROM trajectory_points
            WHERE getTimestamp(heading_instant) = getTimestamp(pos_instant)
        ),
        grid_heading AS (
            SELECT 
                ST_SnapToGrid(ST_MakePoint(lon, lat), :grid_size) as grid_point,
                AVG(heading) as avg_heading,
                COUNT(*) as point_count,
                STDDEV(heading) as heading_std
            FROM point_data
            GROUP BY ST_SnapToGrid(ST_MakePoint(lon, lat), :grid_size)
            HAVING COUNT(*) >= 5
        )
        SELECT 
            ST_X(grid_point) as lon,
            ST_Y(grid_point) as lat,
            avg_heading,
            point_count,
            heading_std
        FROM grid_heading
        ORDER BY point_count DESC
        LIMIT 1000
    """)
    
    result = await db.execute(query, params)
    rows = result.fetchall()
    
    return {
        "grid_size": grid_size,
        "total_cells": len(rows),
        "heading_data": [
            {
                "lon": r[0],
                "lat": r[1],
                "avg_heading": round(r[2], 2) if r[2] else None,
                "point_count": r[3],
                "heading_std": round(r[4], 2) if r[4] else None
            }
            for r in rows
        ]
    }
