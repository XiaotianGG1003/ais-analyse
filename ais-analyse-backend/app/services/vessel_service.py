from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vessel import (
    VesselBrief,
    VesselDetail,
    VesselListItem,
    VesselListResponse,
    LastPosition,
    TrackResponse,
)


async def search_vessels(
    db: AsyncSession, keyword: str, limit: int = 20
) -> list[VesselBrief]:
    """按 MMSI 或船名搜索船舶"""
    # 判断是否为纯数字（MMSI搜索）
    if keyword.isdigit():
        query = text("""
            SELECT DISTINCT mmsi, vessel_name, vessel_type, length, width
            FROM ais_raw
            WHERE mmsi = :mmsi
            LIMIT :limit
        """)
        result = await db.execute(query, {"mmsi": int(keyword), "limit": limit})
    else:
        query = text("""
            SELECT DISTINCT ON (mmsi) mmsi, vessel_name, vessel_type, length, width
            FROM ais_raw
            WHERE vessel_name ILIKE :pattern
            ORDER BY mmsi
            LIMIT :limit
        """)
        result = await db.execute(query, {"pattern": f"%{keyword}%", "limit": limit})

    return [
        VesselBrief(
            mmsi=row.mmsi,
            vessel_name=row.vessel_name,
            vessel_type=row.vessel_type,
            length=row.length,
            width=row.width,
        )
        for row in result.fetchall()
    ]


async def get_vessel_detail(db: AsyncSession, mmsi: int) -> VesselDetail | None:
    """获取船舶详情（含最新位置）"""
    query = text("""
        SELECT mmsi, vessel_name, imo, call_sign, vessel_type, status,
               length, width, draft,
               longitude, latitude, base_date_time, sog, cog
        FROM ais_raw
        WHERE mmsi = :mmsi
        ORDER BY base_date_time DESC
        LIMIT 1
    """)
    result = await db.execute(query, {"mmsi": mmsi})
    row = result.fetchone()
    if not row:
        return None

    last_pos = None
    if row.longitude is not None and row.latitude is not None:
        last_pos = LastPosition(
            longitude=row.longitude,
            latitude=row.latitude,
            timestamp=row.base_date_time,
            sog=row.sog,
            cog=row.cog,
        )

    return VesselDetail(
        mmsi=row.mmsi,
        vessel_name=row.vessel_name,
        imo=row.imo,
        call_sign=row.call_sign,
        vessel_type=row.vessel_type,
        status=row.status,
        length=row.length,
        width=row.width,
        draft=row.draft,
        last_position=last_pos,
    )


async def list_vessels(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    vessel_type: int | None = None,
) -> VesselListResponse:
    """分页查询船舶列表"""
    offset = (page - 1) * page_size

    # 总数
    count_query = text("SELECT COUNT(DISTINCT mmsi) FROM ais_raw")
    if vessel_type is not None:
        count_query = text(
            "SELECT COUNT(DISTINCT mmsi) FROM ais_raw WHERE vessel_type = :vtype"
        )
    count_result = await db.execute(
        count_query, {"vtype": vessel_type} if vessel_type is not None else {}
    )
    total = count_result.scalar() or 0

    # 列表
    where_clause = "WHERE vessel_type = :vtype" if vessel_type is not None else ""
    list_query = text(f"""
        SELECT DISTINCT ON (mmsi)
            mmsi, vessel_name, vessel_type, length, width, base_date_time AS last_time
        FROM ais_raw
        {where_clause}
        ORDER BY mmsi, base_date_time DESC
        LIMIT :limit OFFSET :offset
    """)
    params: dict = {"limit": page_size, "offset": offset}
    if vessel_type is not None:
        params["vtype"] = vessel_type
    result = await db.execute(list_query, params)

    items = [
        VesselListItem(
            mmsi=row.mmsi,
            vessel_name=row.vessel_name,
            vessel_type=row.vessel_type,
            length=row.length,
            width=row.width,
            last_time=row.last_time,
        )
        for row in result.fetchall()
    ]

    return VesselListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


async def get_vessel_track(
    db: AsyncSession,
    mmsi: int,
    start_time: datetime,
    end_time: datetime,
) -> TrackResponse | None:
    """查询轨迹（通过 MobilityDB vessels 表）"""
    query = text("""
        SELECT mmsi, vessel_name,
               ST_AsGeoJSON(trajectory(atTime(trip, tstzspan(:t_start, :t_end))))::json AS geojson
        FROM vessels
        WHERE mmsi = :mmsi
    """)
    result = await db.execute(
        query, {"mmsi": mmsi, "t_start": start_time, "t_end": end_time}
    )
    row = result.fetchone()
    if not row or row.geojson is None:
        return None

    geojson = row.geojson
    coords = geojson.get("coordinates", [])

    return TrackResponse(
        mmsi=row.mmsi,
        vessel_name=row.vessel_name,
        track=geojson,
        point_count=len(coords),
    )
