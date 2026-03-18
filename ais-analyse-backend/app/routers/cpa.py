from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
import math

from app.database import get_db

router = APIRouter(prefix="/api/cpa", tags=["cpa"])


@router.get("/analyze", response_model=dict)
async def analyze_closest_point_approach(
    mmsi_a: int,
    mmsi_b: int,
    db: AsyncSession = Depends(get_db),
):
    """
    分析两艘船舶的最近接近点 (CPA - Closest Point of Approach)
    使用 vessels 表和 MobilityDB 函数
    """
    if not (100000000 <= mmsi_a <= 999999999):
        raise HTTPException(status_code=400, detail="MMSI A 格式无效")
    if not (100000000 <= mmsi_b <= 999999999):
        raise HTTPException(status_code=400, detail="MMSI B 格式无效")
    
    if mmsi_a == mmsi_b:
        raise HTTPException(status_code=400, detail="两艘船舶不能相同")
    
    # Check if ais_raw table exists
    check_table = text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'ais_raw'
        )
    """)
    result = await db.execute(check_table)
    if not result.scalar():
        raise HTTPException(status_code=500, detail="ais_raw 表不存在")
    
    # Check if vessels exist in ais_raw
    check_query = text("""
        SELECT 
            (SELECT COUNT(*) FROM ais_raw WHERE mmsi = :mmsi_a) as count_a,
            (SELECT COUNT(*) FROM ais_raw WHERE mmsi = :mmsi_b) as count_b,
            (SELECT vessel_name FROM ais_raw WHERE mmsi = :mmsi_a LIMIT 1) as name_a,
            (SELECT vessel_name FROM ais_raw WHERE mmsi = :mmsi_b LIMIT 1) as name_b
    """)
    
    result = await db.execute(check_query, {"mmsi_a": mmsi_a, "mmsi_b": mmsi_b})
    row = result.fetchone()
    
    if row.count_a == 0:
        raise HTTPException(status_code=404, detail=f"船舶 {mmsi_a} 不存在或没有轨迹数据")
    if row.count_b == 0:
        raise HTTPException(status_code=404, detail=f"船舶 {mmsi_b} 不存在或没有轨迹数据")
    
    vessel_name_a = row.name_a or f"船舶 {mmsi_a}"
    vessel_name_b = row.name_b or f"船舶 {mmsi_b}"
    
    # CPA Analysis using ais_raw table (more reliable than vessels/MobilityDB)
    query = text("""
        WITH vessel_a_points AS (
            SELECT base_date_time, latitude, longitude, sog, vessel_name
            FROM ais_raw
            WHERE mmsi = :mmsi_a
        ),
        vessel_b_points AS (
            SELECT base_date_time, latitude, longitude, sog, vessel_name
            FROM ais_raw
            WHERE mmsi = :mmsi_b
        ),
        -- 对齐时间戳（找时间差 < 60 秒的点对）
        aligned_points AS (
            SELECT 
                a.base_date_time as time_a,
                a.latitude as lat_a,
                a.longitude as lon_a,
                a.sog as sog_a,
                a.vessel_name as name_a,
                b.base_date_time as time_b,
                b.latitude as lat_b,
                b.longitude as lon_b,
                b.sog as sog_b,
                b.vessel_name as name_b,
                ABS(EXTRACT(EPOCH FROM (a.base_date_time - b.base_date_time))) as time_diff_sec
            FROM vessel_a_points a
            JOIN vessel_b_points b 
                ON ABS(EXTRACT(EPOCH FROM (a.base_date_time - b.base_date_time))) <= 60
        ),
        -- 计算距离（使用Haversine近似）
        distances AS (
            SELECT 
                time_a, lat_a, lon_a, sog_a, name_a,
                time_b, lat_b, lon_b, sog_b, name_b,
                time_diff_sec,
                -- 计算Haversine距离（米）
                2 * 6371000 * ASIN(SQRT(
                    POW(SIN(RADIANS(lat_b - lat_a) / 2), 2) +
                    COS(RADIANS(lat_a)) * COS(RADIANS(lat_b)) * 
                    POW(SIN(RADIANS(lon_b - lon_a) / 2), 2)
                )) as dist_m
            FROM aligned_points
        ),
        -- 找到最小距离
        min_distance AS (
            SELECT * FROM distances
            ORDER BY dist_m ASC
            LIMIT 1
        )
        SELECT 
            time_a as cpa_time,
            dist_m as min_distance_m,
            lat_a, lon_a, sog_a, name_a,
            lat_b, lon_b, sog_b, name_b,
            time_diff_sec
        FROM min_distance
    """)
    
    try:
        result = await db.execute(query, {"mmsi_a": mmsi_a, "mmsi_b": mmsi_b})
        row = result.fetchone()
        
        if not row or row.cpa_time is None:
            raise HTTPException(status_code=404, detail="无法计算最近接近点，两船轨迹可能没有时空交集")
        
        # Get positions and distance from query result
        lon_a, lat_a = row.lon_a, row.lat_a
        lon_b, lat_b = row.lon_b, row.lat_b
        sog_a, sog_b = row.sog_a or 0, row.sog_b or 0
        min_distance_m = row.min_distance_m
        
        if lon_a is None or lat_a is None or lon_b is None or lat_b is None:
            raise HTTPException(status_code=500, detail="无法获取CPA时刻的船舶位置")
        
        # Convert to nautical miles
        min_distance_nm = min_distance_m / 1852.0
        
        # Determine safety status
        if min_distance_nm < 0.5:
            safety_status = "danger"
            safety_text = "Danger"
        elif min_distance_nm < 1.0:
            safety_status = "warning"
            safety_text = "Warning"
        else:
            safety_status = "safe"
            safety_text = "Safe"
        
        # Build shortest line from actual positions at CPA time
        shortest_line = {
            "a": {"lon": lon_a, "lat": lat_a},
            "b": {"lon": lon_b, "lat": lat_b}
        }
        
        # Use vessel names from query result if available
        name_a = row.name_a or vessel_name_a
        name_b = row.name_b or vessel_name_b
        
        return {
            "code": 200,
            "data": {
                "mmsi_a": mmsi_a,
                "name_a": name_a,
                "mmsi_b": mmsi_b,
                "name_b": name_b,
                "cpa_time": row.cpa_time.isoformat() if hasattr(row.cpa_time, 'isoformat') else str(row.cpa_time),
                "min_distance_m": round(min_distance_m, 2),
                "min_distance_nm": round(min_distance_nm, 2),
                "safety_status": safety_status,
                "safety_text": safety_text,
                "position_a": {"lon": lon_a, "lat": lat_a},
                "position_b": {"lon": lon_b, "lat": lat_b},
                "sog_a": sog_a,
                "sog_b": sog_b,
                "shortest_line": shortest_line,
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"数据库查询失败: {str(e)}")


@router.get("/distance-series", response_model=dict)
async def get_distance_series(
    mmsi_a: int,
    mmsi_b: int,
    db: AsyncSession = Depends(get_db),
):
    """
    获取两船距离随时间变化的序列
    """
    if not (100000000 <= mmsi_a <= 999999999):
        raise HTTPException(status_code=400, detail="MMSI A 格式无效")
    if not (100000000 <= mmsi_b <= 999999999):
        raise HTTPException(status_code=400, detail="MMSI B 格式无效")
    
    return {
        "code": 200,
        "data": {
            "mmsi_a": mmsi_a,
            "mmsi_b": mmsi_b,
            "note": "距离序列功能需要进一步实现",
        }
    }
