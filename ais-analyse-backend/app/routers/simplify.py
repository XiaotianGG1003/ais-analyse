"""
轨迹压缩简化模块
使用 PostGIS ST_Simplify 实现 Douglas-Peucker 算法
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
import math

from app.database import get_db

router = APIRouter(prefix="/api/simplify", tags=["simplify"])


def douglas_peucker(points: List[dict], epsilon: float) -> List[dict]:
    """
    Douglas-Peucker 算法实现
    points: [{lon, lat, timestamp}, ...]
    epsilon: 距离阈值（度）
    """
    if len(points) <= 2:
        return points
    
    def perpendicular_distance(point, line_start, line_end):
        """计算点到线段的垂直距离"""
        x0, y0 = point['lon'], point['lat']
        x1, y1 = line_start['lon'], line_start['lat']
        x2, y2 = line_end['lon'], line_end['lat']
        
        if x1 == x2 and y1 == y2:
            return math.sqrt((x0 - x1) ** 2 + (y0 - y1) ** 2)
        
        num = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1)
        den = math.sqrt((y2 - y1) ** 2 + (x2 - x1) ** 2)
        return num / den
    
    def dp_recursive(start_idx, end_idx):
        """递归简化"""
        if end_idx <= start_idx + 1:
            return [points[start_idx]] if start_idx == end_idx else [points[start_idx], points[end_idx]]
        
        max_dist = 0
        max_idx = start_idx
        
        for i in range(start_idx + 1, end_idx):
            dist = perpendicular_distance(points[i], points[start_idx], points[end_idx])
            if dist > max_dist:
                max_dist = dist
                max_idx = i
        
        if max_dist > epsilon:
            left = dp_recursive(start_idx, max_idx)
            right = dp_recursive(max_idx, end_idx)
            return left[:-1] + right
        else:
            return [points[start_idx], points[end_idx]]
    
    return dp_recursive(0, len(points) - 1)


@router.get("/{mmsi}", response_model=dict)
async def simplify_trajectory(
    mmsi: int,
    tolerance: float = Query(100.0, description="简化容差（米），越大压缩率越高"),
    start_time: Optional[str] = Query(None, description="开始时间 (ISO格式)"),
    end_time: Optional[str] = Query(None, description="结束时间 (ISO格式)"),
    db: AsyncSession = Depends(get_db),
):
    """
    使用 Douglas-Peucker 算法压缩船舶轨迹
    
    参数:
    - tolerance: 简化容差（米），推荐值：50-500米
      - 50m: 高精度，保留较多点
      - 100m: 平衡（默认）
      - 500m: 高压缩，只保留关键点
    """
    # 验证 MMSI
    if not (100000000 <= mmsi <= 999999999):
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    
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
    
    # 获取船舶信息
    check_ship = text("""
        SELECT vessel_name, trip, 
               numInstants(trip) as original_points,
               startTimestamp(trip) as trip_start,
               endTimestamp(trip) as trip_end
        FROM vessels 
        WHERE mmsi = :mmsi AND trip IS NOT NULL
    """)
    result = await db.execute(check_ship, {"mmsi": mmsi})
    ship = result.fetchone()
    
    if not ship:
        raise HTTPException(status_code=404, detail=f"船舶 {mmsi} 不存在或没有轨迹数据")
    
    vessel_name = ship.vessel_name or f"船舶 {mmsi}"
    original_points_count = ship.original_points
    trip_start = ship.trip_start
    trip_end = ship.trip_end
    
    # 获取轨迹点
    query = text("""
        SELECT 
            ST_X(valueAtTimestamp(trip, ti)::geometry) as lon,
            ST_Y(valueAtTimestamp(trip, ti)::geometry) as lat,
            ti as timestamp
        FROM vessels,
             unnest(timestamps(trip)) as ti
        WHERE mmsi = :mmsi
        ORDER BY ti
    """)
    
    try:
        result = await db.execute(query, {"mmsi": mmsi})
        rows = result.fetchall()
        
        # 构建点列表
        original_path = [
            {"lon": r.lon, "lat": r.lat, "timestamp": r.timestamp.isoformat() if hasattr(r.timestamp, 'isoformat') else str(r.timestamp)}
            for r in rows
        ]
        
        if len(original_path) < 3:
            # 点太少，无需简化
            return {
                "code": 200,
                "data": {
                    "mmsi": mmsi,
                    "vessel_name": vessel_name,
                    "tolerance_m": tolerance,
                    "original_points": len(original_path),
                    "simplified_points": len(original_path),
                    "compression_rate": 0.0,
                    "time_range": {
                        "start": trip_start.isoformat() if hasattr(trip_start, 'isoformat') else str(trip_start),
                        "end": trip_end.isoformat() if hasattr(trip_end, 'isoformat') else str(trip_end),
                    },
                    "bounds": {
                        "min_lon": min(p["lon"] for p in original_path) if original_path else 0,
                        "min_lat": min(p["lat"] for p in original_path) if original_path else 0,
                        "max_lon": max(p["lon"] for p in original_path) if original_path else 0,
                        "max_lat": max(p["lat"] for p in original_path) if original_path else 0,
                    },
                    "original_path": original_path,
                    "simplified_path": original_path,
                }
            }
        
        # 将米转换为度（近似）
        # 在纬度方向上，1度 ≈ 111km
        # 在经度方向上，1度 ≈ 111km * cos(lat)
        avg_lat = sum(p["lat"] for p in original_path) / len(original_path)
        lat_factor = math.cos(math.radians(avg_lat))
        
        # 转换容差为度（取纬度和经度的较小值作为限制）
        tolerance_deg = tolerance / 111000.0
        
        # 应用 Douglas-Peucker 算法
        simplified_path = douglas_peucker(original_path, tolerance_deg)
        
        # 计算压缩率
        compression_rate = round((1.0 - len(simplified_path) / len(original_path)) * 100, 2)
        
        return {
            "code": 200,
            "data": {
                "mmsi": mmsi,
                "vessel_name": vessel_name,
                "tolerance_m": tolerance,
                "original_points": len(original_path),
                "simplified_points": len(simplified_path),
                "compression_rate": compression_rate,
                "time_range": {
                    "start": trip_start.isoformat() if hasattr(trip_start, 'isoformat') else str(trip_start),
                    "end": trip_end.isoformat() if hasattr(trip_end, 'isoformat') else str(trip_end),
                },
                "bounds": {
                    "min_lon": min(p["lon"] for p in original_path),
                    "min_lat": min(p["lat"] for p in original_path),
                    "max_lon": max(p["lon"] for p in original_path),
                    "max_lat": max(p["lat"] for p in original_path),
                },
                "original_path": original_path,
                "simplified_path": simplified_path,
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"轨迹简化失败: {str(e)}")


@router.get("/{mmsi}/comparison", response_model=dict)
async def compare_simplification(
    mmsi: int,
    tolerances: str = Query("50,100,200,500", description="多个容差值对比，逗号分隔（米）"),
    db: AsyncSession = Depends(get_db),
):
    """
    对比不同容差值的简化效果
    """
    if not (100000000 <= mmsi <= 999999999):
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    
    try:
        tolerance_list = [float(t.strip()) for t in tolerances.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="容差值格式无效")
    
    # 获取原始点数
    check_query = text("""
        SELECT numInstants(trip) as original_points,
               startTimestamp(trip) as trip_start,
               endTimestamp(trip) as trip_end
        FROM vessels 
        WHERE mmsi = :mmsi AND trip IS NOT NULL
    """)
    result = await db.execute(check_query, {"mmsi": mmsi})
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="船舶不存在或没有轨迹数据")
    
    original_points_count = row.original_points
    trip_start = row.trip_start
    trip_end = row.trip_end
    
    # 获取轨迹点
    query = text("""
        SELECT 
            ST_X(valueAtTimestamp(trip, ti)::geometry) as lon,
            ST_Y(valueAtTimestamp(trip, ti)::geometry) as lat
        FROM vessels,
             unnest(timestamps(trip)) as ti
        WHERE mmsi = :mmsi
        ORDER BY ti
    """)
    
    result = await db.execute(query, {"mmsi": mmsi})
    rows = result.fetchall()
    
    original_path = [{"lon": r.lon, "lat": r.lat} for r in rows]
    
    if len(original_path) < 3:
        return {
            "code": 200,
            "data": {
                "mmsi": mmsi,
                "original_points": len(original_path),
                "comparisons": [
                    {"tolerance_m": t, "simplified_points": len(original_path), "compression_rate": 0.0}
                    for t in tolerance_list
                ],
            }
        }
    
    # 计算平均纬度
    avg_lat = sum(p["lat"] for p in original_path) / len(original_path)
    
    # 对比不同容差
    comparisons = []
    for tolerance in tolerance_list:
        tolerance_deg = tolerance / 111000.0
        simplified_path = douglas_peucker(original_path, tolerance_deg)
        simplified_points = len(simplified_path)
        compression_rate = round((1.0 - simplified_points / len(original_path)) * 100, 2)
        
        comparisons.append({
            "tolerance_m": tolerance,
            "simplified_points": simplified_points,
            "compression_rate": compression_rate,
        })
    
    return {
        "code": 200,
        "data": {
            "mmsi": mmsi,
            "original_points": len(original_path),
            "comparisons": comparisons,
        }
    }
