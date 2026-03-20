from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.port import (
    PortAnalysisResponse,
    PortBBox,
    PortItem,
    PortListResponse,
    PortStayVessel,
)


def _to_db_naive_utc(dt: datetime) -> datetime:
    """Convert incoming datetime to naive UTC for TIMESTAMP columns."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


async def list_ports(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
) -> PortListResponse:
    offset = (page - 1) * page_size

    if keyword:
        count_query = text("""
            SELECT COUNT(*)
            FROM ports
            WHERE name ILIKE :keyword
        """)
        count_result = await db.execute(count_query, {"keyword": f"%{keyword}%"})
    else:
        count_query = text("SELECT COUNT(*) FROM ports")
        count_result = await db.execute(count_query)

    total = int(count_result.scalar() or 0)

    if keyword:
        list_query = text("""
            SELECT
                id,
                name,
                bbox_min_lon,
                bbox_min_lat,
                bbox_max_lon,
                bbox_max_lat,
                ST_AsGeoJSON(area_geom)::json AS polygon,
                created_at
            FROM ports
            WHERE name ILIKE :keyword
            ORDER BY created_at DESC, id DESC
            LIMIT :limit OFFSET :offset
        """)
        result = await db.execute(
            list_query,
            {"keyword": f"%{keyword}%", "limit": page_size, "offset": offset},
        )
    else:
        list_query = text("""
            SELECT
                id,
                name,
                bbox_min_lon,
                bbox_min_lat,
                bbox_max_lon,
                bbox_max_lat,
                ST_AsGeoJSON(area_geom)::json AS polygon,
                created_at
            FROM ports
            ORDER BY created_at DESC, id DESC
            LIMIT :limit OFFSET :offset
        """)
        result = await db.execute(list_query, {"limit": page_size, "offset": offset})

    items = [
        PortItem(
            id=row.id,
            name=row.name,
            bbox=PortBBox(
                min_lon=float(row.bbox_min_lon),
                min_lat=float(row.bbox_min_lat),
                max_lon=float(row.bbox_max_lon),
                max_lat=float(row.bbox_max_lat),
            ),
            polygon=row.polygon,
            created_at=row.created_at,
        )
        for row in result.fetchall()
    ]

    return PortListResponse(total=total, page=page, page_size=page_size, items=items)


async def create_port(
    db: AsyncSession,
    name: str,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> PortItem:
    query = text("""
        INSERT INTO ports (
            name,
            area_geom,
            bbox_min_lon,
            bbox_min_lat,
            bbox_max_lon,
            bbox_max_lat
        )
        VALUES (
            :name,
            ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326),
            :min_lon,
            :min_lat,
            :max_lon,
            :max_lat
        )
        RETURNING
            id,
            name,
            bbox_min_lon,
            bbox_min_lat,
            bbox_max_lon,
            bbox_max_lat,
            ST_AsGeoJSON(area_geom)::json AS polygon,
            created_at
    """)
    result = await db.execute(
        query,
        {
            "name": name,
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat,
        },
    )
    await db.commit()
    row = result.fetchone()
    return PortItem(
        id=row.id,
        name=row.name,
        bbox=PortBBox(
            min_lon=float(row.bbox_min_lon),
            min_lat=float(row.bbox_min_lat),
            max_lon=float(row.bbox_max_lon),
            max_lat=float(row.bbox_max_lat),
        ),
        polygon=row.polygon,
        created_at=row.created_at,
    )


async def delete_port(db: AsyncSession, port_id: int) -> bool:
    query = text("DELETE FROM ports WHERE id = :port_id")
    result = await db.execute(query, {"port_id": port_id})
    await db.commit()
    return result.rowcount > 0


async def get_port_analysis(
    db: AsyncSession,
    port_id: int,
    start_time: datetime,
    end_time: datetime,
    top_n: int = 5,
) -> PortAnalysisResponse | None:
    db_start_time = _to_db_naive_utc(start_time)
    db_end_time = _to_db_naive_utc(end_time)

    port_query = text("SELECT id, name FROM ports WHERE id = :port_id")
    port_result = await db.execute(port_query, {"port_id": port_id})
    port = port_result.fetchone()
    if not port:
        return None

    metrics_query = text("""
        WITH points AS (
            SELECT
                r.mmsi,
                r.base_date_time AS ts,
                ST_Contains(
                    p.area_geom,
                    ST_SetSRID(ST_Point(r.longitude, r.latitude), 4326)
                ) AS inside
            FROM ais_raw r
            JOIN ports p ON p.id = :port_id
            WHERE r.base_date_time >= :start_time
              AND r.base_date_time <= :end_time
              AND r.longitude IS NOT NULL
              AND r.latitude IS NOT NULL
        ),
        ordered AS (
            SELECT
                mmsi,
                ts,
                inside,
                LAG(inside) OVER (PARTITION BY mmsi ORDER BY ts) AS prev_inside
            FROM points
        ),
        entry_points AS (
            SELECT
                mmsi,
                ts AS entry_ts,
                ROW_NUMBER() OVER (PARTITION BY mmsi ORDER BY ts) AS rn
            FROM ordered
            WHERE inside = TRUE AND COALESCE(prev_inside, FALSE) = FALSE
        ),
        exit_points AS (
            SELECT
                mmsi,
                ts AS exit_ts,
                ROW_NUMBER() OVER (PARTITION BY mmsi ORDER BY ts) AS rn
            FROM ordered
            WHERE inside = FALSE AND prev_inside = TRUE
        ),
        stays AS (
            SELECT
                e.mmsi,
                e.entry_ts,
                COALESCE(x.exit_ts, :end_time) AS exit_ts,
                GREATEST(EXTRACT(EPOCH FROM (COALESCE(x.exit_ts, :end_time) - e.entry_ts)), 0) AS duration_seconds
            FROM entry_points e
            LEFT JOIN exit_points x
              ON x.mmsi = e.mmsi
             AND x.rn = e.rn
        )
        SELECT
            (SELECT COUNT(DISTINCT mmsi) FROM ordered WHERE inside = TRUE) AS unique_vessel_count,
            (SELECT COUNT(*) FROM entry_points) AS entry_count,
            (SELECT COUNT(*) FROM exit_points) AS exit_count,
            COALESCE((SELECT SUM(duration_seconds) FROM stays), 0) / 60.0 AS total_stay_minutes,
            COALESCE((SELECT AVG(duration_seconds) FROM stays), 0) / 60.0 AS avg_stay_minutes
    """)

    metrics_result = await db.execute(
        metrics_query,
        {"port_id": port_id, "start_time": db_start_time, "end_time": db_end_time},
    )
    metrics = metrics_result.fetchone()
    if not metrics:
        return None

    top_query = text("""
        WITH points AS (
            SELECT
                r.mmsi,
                r.base_date_time AS ts,
                ST_Contains(
                    p.area_geom,
                    ST_SetSRID(ST_Point(r.longitude, r.latitude), 4326)
                ) AS inside
            FROM ais_raw r
            JOIN ports p ON p.id = :port_id
            WHERE r.base_date_time >= :start_time
              AND r.base_date_time <= :end_time
              AND r.longitude IS NOT NULL
              AND r.latitude IS NOT NULL
        ),
        ordered AS (
            SELECT
                mmsi,
                ts,
                inside,
                LAG(inside) OVER (PARTITION BY mmsi ORDER BY ts) AS prev_inside
            FROM points
        ),
        entry_points AS (
            SELECT
                mmsi,
                ts AS entry_ts,
                ROW_NUMBER() OVER (PARTITION BY mmsi ORDER BY ts) AS rn
            FROM ordered
            WHERE inside = TRUE AND COALESCE(prev_inside, FALSE) = FALSE
        ),
        exit_points AS (
            SELECT
                mmsi,
                ts AS exit_ts,
                ROW_NUMBER() OVER (PARTITION BY mmsi ORDER BY ts) AS rn
            FROM ordered
            WHERE inside = FALSE AND prev_inside = TRUE
        ),
        stays AS (
            SELECT
                e.mmsi,
                GREATEST(EXTRACT(EPOCH FROM (COALESCE(x.exit_ts, :end_time) - e.entry_ts)), 0) AS duration_seconds
            FROM entry_points e
            LEFT JOIN exit_points x
              ON x.mmsi = e.mmsi
             AND x.rn = e.rn
        ),
        vessel_name_latest AS (
            SELECT DISTINCT ON (mmsi)
                mmsi,
                vessel_name
            FROM ais_raw
            WHERE vessel_name IS NOT NULL
            ORDER BY mmsi, base_date_time DESC
        )
        SELECT
            s.mmsi,
            v.vessel_name,
            SUM(s.duration_seconds) / 60.0 AS stay_minutes,
            COUNT(*) AS visit_count
        FROM stays s
        LEFT JOIN vessel_name_latest v ON v.mmsi = s.mmsi
        GROUP BY s.mmsi, v.vessel_name
        ORDER BY stay_minutes DESC, visit_count DESC
        LIMIT :top_n
    """)

    top_result = await db.execute(
        top_query,
        {
            "port_id": port_id,
            "start_time": db_start_time,
            "end_time": db_end_time,
            "top_n": top_n,
        },
    )

    top_vessels = [
        PortStayVessel(
            mmsi=int(row.mmsi),
            vessel_name=row.vessel_name,
            stay_minutes=round(float(row.stay_minutes or 0), 1),
            visit_count=int(row.visit_count or 0),
        )
        for row in top_result.fetchall()
    ]

    return PortAnalysisResponse(
        port_id=int(port.id),
        port_name=port.name,
        start_time=db_start_time,
        end_time=db_end_time,
        unique_vessel_count=int(metrics.unique_vessel_count or 0),
        entry_count=int(metrics.entry_count or 0),
        exit_count=int(metrics.exit_count or 0),
        total_stay_minutes=round(float(metrics.total_stay_minutes or 0), 1),
        avg_stay_minutes=round(float(metrics.avg_stay_minutes or 0), 1),
        top_vessels=top_vessels,
    )
