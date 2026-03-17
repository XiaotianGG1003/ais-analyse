from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(prefix="/api/animation", tags=["animation"])


@router.get("/{mmsi}/frames", response_model=dict)
async def get_animation_frames(
    mmsi: int,
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    step_seconds: int = Query(default=60, ge=10, le=300),
    db: AsyncSession = Depends(get_db),
):
    """
    Get trajectory animation frame data using pure SQL interpolation
    """
    if mmsi < 100000000 or mmsi > 999999999:
        raise HTTPException(status_code=400, detail="Invalid MMSI format")
    
    # Remove timezone info
    start_time = start_time.replace(tzinfo=None) if start_time.tzinfo else start_time
    end_time = end_time.replace(tzinfo=None) if end_time.tzinfo else end_time
    
    # Check if data exists (using bound parameters for safety)
    check_query = text("""
        SELECT COUNT(*) FROM ais_raw 
        WHERE mmsi = :mmsi 
          AND base_date_time BETWEEN :start AND :end
          AND latitude IS NOT NULL 
          AND longitude IS NOT NULL
    """)
    result = await db.execute(check_query, {
        "mmsi": mmsi,
        "start": start_time,
        "end": end_time
    })
    count = result.scalar()
    
    if count == 0:
        raise HTTPException(status_code=404, detail="No trajectory data found for the specified time range")
    
    # Format timestamps for SQL (safely)
    start_str = start_time.strftime('%Y-%m-%d %H:%M:%S')
    end_str = end_time.strftime('%Y-%m-%d %H:%M:%S')
    
    # Approach: Use LATERAL join instead of correlated subqueries
    query = text(f"""
        WITH time_points AS (
            SELECT generate_series(
                TIMESTAMP '{start_str}',
                TIMESTAMP '{end_str}',
                INTERVAL '{step_seconds} seconds'
            ) as t
        ),
        trajectory_points AS (
            SELECT 
                latitude,
                longitude,
                base_date_time,
                sog,
                cog
            FROM ais_raw
            WHERE mmsi = {mmsi}
              AND base_date_time BETWEEN TIMESTAMP '{start_str}' AND TIMESTAMP '{end_str}'
              AND latitude IS NOT NULL 
              AND longitude IS NOT NULL
            ORDER BY base_date_time
        ),
        -- Find previous point for each time point
        prev_points AS (
            SELECT DISTINCT ON (tp.t)
                tp.t as timestamp,
                tr.base_date_time as prev_time,
                tr.latitude as prev_lat,
                tr.longitude as prev_lon,
                tr.sog as prev_sog,
                tr.cog as prev_cog
            FROM time_points tp
            LEFT JOIN trajectory_points tr ON tr.base_date_time <= tp.t
            WHERE tr.base_date_time IS NOT NULL
            ORDER BY tp.t, tr.base_date_time DESC
        ),
        -- Find exact or next point for each time point
        next_points AS (
            SELECT DISTINCT ON (tp.t)
                tp.t as timestamp,
                tr.base_date_time as next_time,
                tr.latitude as next_lat,
                tr.longitude as next_lon
            FROM time_points tp
            LEFT JOIN trajectory_points tr ON tr.base_date_time >= tp.t
            WHERE tr.base_date_time IS NOT NULL
            ORDER BY tp.t, tr.base_date_time ASC
        )
        SELECT 
            p.timestamp,
            CASE 
                WHEN n.next_time = p.timestamp THEN n.next_lat
                WHEN n.next_time IS NOT NULL AND p.prev_time IS NOT NULL THEN
                    p.prev_lat + (n.next_lat - p.prev_lat) * 
                    EXTRACT(EPOCH FROM (p.timestamp - p.prev_time)) / 
                    NULLIF(EXTRACT(EPOCH FROM (n.next_time - p.prev_time)), 0)
                ELSE p.prev_lat
            END as lat,
            CASE 
                WHEN n.next_time = p.timestamp THEN n.next_lon
                WHEN n.next_time IS NOT NULL AND p.prev_time IS NOT NULL THEN
                    p.prev_lon + (n.next_lon - p.prev_lon) * 
                    EXTRACT(EPOCH FROM (p.timestamp - p.prev_time)) / 
                    NULLIF(EXTRACT(EPOCH FROM (n.next_time - p.prev_time)), 0)
                ELSE p.prev_lon
            END as lon,
            p.prev_sog as sog,
            p.prev_cog as cog
        FROM prev_points p
        LEFT JOIN next_points n ON p.timestamp = n.timestamp
        ORDER BY p.timestamp
    """)
    
    try:
        result = await db.execute(query)
        rows = result.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    
    frames = []
    for row in rows:
        if row.lat is not None and row.lon is not None:
            frames.append({
                "timestamp": row.timestamp.isoformat() if isinstance(row.timestamp, datetime) else str(row.timestamp),
                "lat": float(row.lat) if isinstance(row.lat, Decimal) else row.lat,
                "lon": float(row.lon) if isinstance(row.lon, Decimal) else row.lon,
                "sog": float(row.sog) if row.sog and isinstance(row.sog, Decimal) else (row.sog or 0),
                "cog": float(row.cog) if row.cog and isinstance(row.cog, Decimal) else (row.cog or 0),
            })
    
    return {
        "code": 200,
        "data": {
            "mmsi": mmsi,
            "frame_count": len(frames),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "step_seconds": step_seconds,
            "frames": frames,
        }
    }


@router.get("/{mmsi}/range", response_model=dict)
async def get_trajectory_time_range(
    mmsi: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the time range of a vessel's trajectory"""
    if mmsi < 100000000 or mmsi > 999999999:
        raise HTTPException(status_code=400, detail="Invalid MMSI format")
    
    query = text("""
        SELECT 
            MIN(base_date_time) as start_time,
            MAX(base_date_time) as end_time,
            COUNT(*) as point_count
        FROM ais_raw
        WHERE mmsi = :mmsi
          AND latitude IS NOT NULL 
          AND longitude IS NOT NULL
    """)
    
    result = await db.execute(query, {"mmsi": mmsi})
    row = result.fetchone()
    
    if not row or row.start_time is None:
        raise HTTPException(status_code=404, detail="No trajectory data found for this vessel")
    
    return {
        "code": 200,
        "data": {
            "mmsi": mmsi,
            "start_time": row.start_time.isoformat(),
            "end_time": row.end_time.isoformat(),
            "point_count": row.point_count,
        }
    }
