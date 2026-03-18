from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
import re
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
    
    # Check if vessels table exists
    check_table = text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'vessels'
        )
    """)
    result = await db.execute(check_table)
    if not result.scalar():
        raise HTTPException(status_code=500, detail="vessels 表不存在，请先执行 build_vessel_trips()")
    
    # Check if vessels exist
    check_query = text("""
        SELECT 
            (SELECT COUNT(*) FROM vessels WHERE mmsi = :mmsi_a) as count_a,
            (SELECT COUNT(*) FROM vessels WHERE mmsi = :mmsi_b) as count_b,
            (SELECT vessel_name FROM vessels WHERE mmsi = :mmsi_a LIMIT 1) as name_a,
            (SELECT vessel_name FROM vessels WHERE mmsi = :mmsi_b LIMIT 1) as name_b
    """)
    
    result = await db.execute(check_query, {"mmsi_a": mmsi_a, "mmsi_b": mmsi_b})
    row = result.fetchone()
    
    if row.count_a == 0:
        raise HTTPException(status_code=404, detail=f"船舶 {mmsi_a} 不存在或没有轨迹数据")
    if row.count_b == 0:
        raise HTTPException(status_code=404, detail=f"船舶 {mmsi_b} 不存在或没有轨迹数据")
    
    vessel_name_a = row.name_a or f"船舶 {mmsi_a}"
    vessel_name_b = row.name_b or f"船舶 {mmsi_b}"
    
    # CPA Analysis using MobilityDB functions
    query = text("""
        WITH vessel_a AS (
            SELECT mmsi, vessel_name, trip
            FROM vessels
            WHERE mmsi = :mmsi_a
        ),
        vessel_b AS (
            SELECT mmsi, vessel_name, trip
            FROM vessels
            WHERE mmsi = :mmsi_b
        ),
        cpa_calc AS (
            SELECT 
                a.mmsi as mmsi_a,
                a.vessel_name as name_a,
                b.mmsi as mmsi_b,
                b.vessel_name as name_b,
                -- 最近接近时刻 (提取为 timestamp)
                startTimestamp(nearestApproachInstant(a.trip, b.trip)) as cpa_time,
                -- 最小距离（度）
                minValue(a.trip <-> b.trip) as min_distance_deg,
                -- 最近时的连线 WKT
                ST_AsText(shortestLine(a.trip, b.trip)) as shortest_line_wkt
            FROM vessel_a a
            CROSS JOIN vessel_b b
        )
        SELECT * FROM cpa_calc
    """)
    
    try:
        result = await db.execute(query, {"mmsi_a": mmsi_a, "mmsi_b": mmsi_b})
        row = result.fetchone()
        
        if not row or row.cpa_time is None:
            raise HTTPException(status_code=404, detail="无法计算最近接近点，两船轨迹可能没有时空交集")
        
        # Parse shortest line WKT to get positions
        def parse_line_wkt(wkt):
            """Parse LINESTRING(lon1 lat1,lon2 lat2) format"""
            if not wkt:
                return None
            match = re.match(r'LINESTRING(?:\s+Z)?\s*\(([^)]+)\)', wkt)
            if match:
                coords = match.group(1).split(',')
                if len(coords) >= 2:
                    pt1 = coords[0].strip().split()
                    pt2 = coords[1].strip().split()
                    return {
                        "a": {"lon": float(pt1[0]), "lat": float(pt1[1])},
                        "b": {"lon": float(pt2[0]), "lat": float(pt2[1])}
                    }
            return None
        
        shortest_line = parse_line_wkt(row.shortest_line_wkt)
        
        if not shortest_line:
            raise HTTPException(status_code=500, detail="无法解析最短连线位置")
        
        # Get positions
        lon_a, lat_a = shortest_line["a"]["lon"], shortest_line["a"]["lat"]
        lon_b, lat_b = shortest_line["b"]["lon"], shortest_line["b"]["lat"]
        
        # Calculate distance from shortest line endpoints using Haversine formula
        def haversine_distance(lat1, lon1, lat2, lon2):
            """Calculate distance between two points in meters"""
            R = 6371000  # Earth radius in meters
            phi1 = math.radians(lat1)
            phi2 = math.radians(lat2)
            delta_phi = math.radians(lat2 - lat1)
            delta_lambda = math.radians(lon2 - lon1)
            a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            return R * c
        
        # Calculate actual distance from shortest line endpoints
        min_distance_m = haversine_distance(lat_a, lon_a, lat_b, lon_b)
        min_distance_nm = min_distance_m / 1852.0
        
        # Determine safety status
        if min_distance_nm < 0.5:
            safety_status = "danger"
            safety_text = "危险"
        elif min_distance_nm < 1.0:
            safety_status = "warning"
            safety_text = "警告"
        else:
            safety_status = "safe"
            safety_text = "安全"
        
        return {
            "code": 200,
            "data": {
                "mmsi_a": mmsi_a,
                "name_a": vessel_name_a,
                "mmsi_b": mmsi_b,
                "name_b": vessel_name_b,
                "cpa_time": row.cpa_time.isoformat() if hasattr(row.cpa_time, 'isoformat') else str(row.cpa_time),
                "min_distance_m": round(min_distance_m, 2),
                "min_distance_nm": round(min_distance_nm, 2),
                "safety_status": safety_status,
                "safety_text": safety_text,
                "position_a": {"lon": lon_a, "lat": lat_a},
                "position_b": {"lon": lon_b, "lat": lat_b},
                "sog_a": 0,
                "sog_b": 0,
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
