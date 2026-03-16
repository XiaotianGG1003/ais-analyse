from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(prefix="/api/stops", tags=["stops"])


@router.get("/{mmsi}", response_model=dict)
async def detect_stops(
    mmsi: int,
    distance_threshold_m: float = Query(default=500, ge=100, le=5000),
    time_threshold_minutes: int = Query(default=30, ge=5, le=120),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    检测船舶停留点
    
    - distance_threshold_m: 距离阈值（米），判定为同一停留区域的最大距离
    - time_threshold_minutes: 时间阈值（分钟），判定为停留的最短时长
    - start_time/end_time: 可选的时间范围过滤
    """
    if mmsi < 100000000 or mmsi > 999999999:
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    
    # 转换米为度（近似）
    distance_threshold_deg = distance_threshold_m / 111000.0
    
    # 构建时间过滤
    time_filter = ""
    params = {
        "mmsi": mmsi,
        "dist_thresh": distance_threshold_deg,
        "time_thresh_minutes": time_threshold_minutes,
    }
    
    if start_time and end_time:
        # 移除时区信息
        start_time = start_time.replace(tzinfo=None) if start_time.tzinfo else start_time
        end_time = end_time.replace(tzinfo=None) if end_time.tzinfo else end_time
        time_filter = "AND base_date_time BETWEEN :t_start AND :t_end"
        params["t_start"] = start_time
        params["t_end"] = end_time
    
    # SQL 查询：使用窗口函数进行停留点检测
    query = text(f"""
        WITH trajectory_points AS (
            SELECT 
                latitude,
                longitude,
                base_date_time,
                ROW_NUMBER() OVER (ORDER BY base_date_time) as rn
            FROM ais_raw
            WHERE mmsi = :mmsi
              {time_filter}
              AND latitude IS NOT NULL 
              AND longitude IS NOT NULL
            ORDER BY base_date_time
        ),
        point_gaps AS (
            SELECT 
                *,
                CASE 
                    WHEN base_date_time - LAG(base_date_time) OVER (ORDER BY base_date_time) > INTERVAL '30 minutes'
                    THEN 1 
                    ELSE 0 
                END as is_new_segment
            FROM trajectory_points
        ),
        segments AS (
            SELECT 
                *,
                SUM(is_new_segment) OVER (ORDER BY base_date_time) as segment_id
            FROM point_gaps
        ),
        segment_clusters AS (
            SELECT 
                segment_id,
                AVG(latitude) as center_lat,
                AVG(longitude) as center_lon,
                MIN(base_date_time) as start_time,
                MAX(base_date_time) as end_time,
                COUNT(*) as point_count,
                STDDEV(latitude) + STDDEV(longitude) as bbox_size
            FROM segments
            GROUP BY segment_id
        ),
        potential_stops AS (
            SELECT 
                *,
                EXTRACT(EPOCH FROM (end_time - start_time)) / 60.0 as duration_minutes
            FROM segment_clusters
        )
        SELECT 
            start_time,
            end_time,
            duration_minutes,
            center_lat,
            center_lon,
            point_count
        FROM potential_stops
        WHERE bbox_size <= :dist_thresh
          AND duration_minutes >= :time_thresh_minutes
        ORDER BY start_time
    """)
    
    try:
        result = await db.execute(query, params)
        rows = result.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"数据库查询失败: {str(e)}")
    
    stops = []
    for row in rows:
        stops.append({
            "startTime": row.start_time.isoformat() if row.start_time else None,
            "endTime": row.end_time.isoformat() if row.end_time else None,
            "durationMinutes": float(row.duration_minutes) if isinstance(row.duration_minutes, Decimal) else row.duration_minutes,
            "lat": float(row.center_lat) if isinstance(row.center_lat, Decimal) else row.center_lat,
            "lon": float(row.center_lon) if isinstance(row.center_lon, Decimal) else row.center_lon,
            "pointCount": row.point_count,
        })
    
    # 计算总停留时长
    total_duration = sum([stop["durationMinutes"] for stop in stops])
    
    return {
        "code": 200,
        "data": {
            "mmsi": mmsi,
            "stop_count": len(stops),
            "total_duration_minutes": total_duration,
            "stops": stops,
        }
    }
