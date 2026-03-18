import json
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import (
    TrackStatistics,
    AreaDetectionRequest,
    AreaDetectionResponse,
    DistanceResponse,
    ManualTrackPoint,
    PredictionResponse,
)
from app.services.predictor_service import predict_from_manual_points
from app.utils.geo import ms_to_knots, meters_to_km


async def get_track_statistics(
    db: AsyncSession,
    mmsi: int,
    start_time: datetime,
    end_time: datetime,
) -> TrackStatistics | None:
    """航行统计（距离、时长、速度等）"""
    query = text("""
        SELECT 
            mmsi,
            length(atTime(trip, span(CAST(:t_start AS timestamptz), CAST(:t_end AS timestamptz), true, true))) AS distance_m,
            duration(atTime(trip, span(CAST(:t_start AS timestamptz), CAST(:t_end AS timestamptz), true, true))) AS duration_interval,
            maxValue(speed(atTime(trip, span(CAST(:t_start AS timestamptz), CAST(:t_end AS timestamptz), true, true)))) AS max_speed_ms,
            twAvg(speed(atTime(trip, span(CAST(:t_start AS timestamptz), CAST(:t_end AS timestamptz), true, true)))) AS avg_speed_ms
        FROM vessels
        WHERE mmsi = :mmsi
    """)
    result = await db.execute(
        query, {"mmsi": mmsi, "t_start": start_time, "t_end": end_time}
    )
    row = result.fetchone()
    if not row or row.distance_m is None:
        return None

    duration_hours = 0.0
    if row.duration_interval is not None:
        duration_hours = row.duration_interval.total_seconds() / 3600.0

    # 速度时序
    speed_query = text("""
        WITH s AS (
            SELECT speed(atTime(trip, span(CAST(:t_start AS timestamptz), CAST(:t_end AS timestamptz), true, true))) AS sp
            FROM vessels
            WHERE mmsi = :mmsi
            ),
        ts AS (
            SELECT unnest(timestamps(sp)) AS ts, sp
            FROM s
        )
        SELECT
            ts,
            valueAtTimestamp(sp, ts) AS speed_ms
        FROM ts
        ORDER BY ts;
    """)
    speed_result = await db.execute(
        speed_query, {"mmsi": mmsi, "t_start": start_time, "t_end": end_time}
    )
    speed_series = [
        {"time": str(r.ts), "speed": round(ms_to_knots(r.speed_ms), 1)}
        for r in speed_result.fetchall()
        if r.speed_ms is not None
    ]

    return TrackStatistics(
        mmsi=mmsi,
        distance_km=round(meters_to_km(row.distance_m), 2),
        duration_hours=round(duration_hours, 2),
        max_speed_knots=round(ms_to_knots(row.max_speed_ms or 0), 1),
        avg_speed_knots=round(ms_to_knots(row.avg_speed_ms or 0), 1),
        speed_series=speed_series,
    )


async def detect_area(
    db: AsyncSession, req: AreaDetectionRequest
) -> AreaDetectionResponse:
    """区域检测 — 判断船舶在指定时间段内是否进入给定区域"""
    area_geojson = json.dumps(req.area)

    # 判断是否相交
    intersect_query = text("""
        SELECT eIntersects(
            atTime(trip, tstzspan(:t_start, :t_end)),
            ST_GeogFromText(ST_AsText(ST_GeomFromGeoJSON(:area_json)))
        ) AS entered
        FROM vessels WHERE mmsi = :mmsi
    """)
    result = await db.execute(
        intersect_query,
        {
            "mmsi": req.mmsi,
            "t_start": req.start_time,
            "t_end": req.end_time,
            "area_json": area_geojson,
        },
    )
    row = result.fetchone()
    entered = bool(row and row.entered)

    if not entered:
        return AreaDetectionResponse(entered=False)

    # 获取区域内轨迹段
    inside_query = text("""
        SELECT
            ST_AsGeoJSON(trajectory(atGeometry(
                atTime(trip, tstzspan(:t_start, :t_end)),
                ST_GeomFromGeoJSON(:area_json)
            )))::json AS inside_track,
            startTimestamp(atGeometry(
                atTime(trip, tstzspan(:t_start, :t_end)),
                ST_GeomFromGeoJSON(:area_json)
            )) AS enter_ts,
            endTimestamp(atGeometry(
                atTime(trip, tstzspan(:t_start, :t_end)),
                ST_GeomFromGeoJSON(:area_json)
            )) AS exit_ts
        FROM vessels WHERE mmsi = :mmsi
    """)
    inside_result = await db.execute(
        inside_query,
        {
            "mmsi": req.mmsi,
            "t_start": req.start_time,
            "t_end": req.end_time,
            "area_json": area_geojson,
        },
    )
    inside_row = inside_result.fetchone()

    enter_time = inside_row.enter_ts if inside_row else None
    exit_time = inside_row.exit_ts if inside_row else None
    stay_minutes = None
    if enter_time and exit_time:
        stay_minutes = round((exit_time - enter_time).total_seconds() / 60.0, 1)

    return AreaDetectionResponse(
        entered=True,
        enter_time=enter_time,
        exit_time=exit_time,
        stay_duration_minutes=stay_minutes,
        inside_track=inside_row.inside_track if inside_row else None,
    )


async def calc_distance(
    db: AsyncSession,
    mmsi1: int,
    mmsi2: int,
    at_time: datetime | None = None,
) -> DistanceResponse | None:
    """两船距离计算"""
    # 历史最近距离
    nad_query = text("""
        SELECT
            nearestApproachDistance(v1.trip, v2.trip) AS min_dist_m,
            nearestApproachInstant(v1.trip, v2.trip) AS closest_time
        FROM vessels v1, vessels v2
        WHERE v1.mmsi = :mmsi1 AND v2.mmsi = :mmsi2
    """)
    result = await db.execute(nad_query, {"mmsi1": mmsi1, "mmsi2": mmsi2})
    row = result.fetchone()
    if not row:
        return None

    min_dist_km = meters_to_km(row.min_dist_m) if row.min_dist_m else 0.0

    # 指定时刻或最新时刻距离
    query_time = at_time or datetime.now(tz=timezone.utc)
    current_query = text("""
        SELECT ST_Distance(
            valueAtTimestamp(v1.trip, :ts),
            valueAtTimestamp(v2.trip, :ts)
        ) AS dist_m
        FROM vessels v1, vessels v2
        WHERE v1.mmsi = :mmsi1 AND v2.mmsi = :mmsi2
    """)
    cur_result = await db.execute(
        current_query, {"mmsi1": mmsi1, "mmsi2": mmsi2, "ts": query_time}
    )
    cur_row = cur_result.fetchone()
    current_dist_km = meters_to_km(cur_row.dist_m) if cur_row and cur_row.dist_m else 0.0

    return DistanceResponse(
        mmsi1=mmsi1,
        mmsi2=mmsi2,
        current_distance_km=round(current_dist_km, 2),
        min_distance_km=round(min_dist_km, 2),
        min_distance_time=row.closest_time,
    )


async def predict_trajectory(
    db: AsyncSession,
    mmsi: int,
    duration_minutes: int = 60,
    step_minutes: int = 5,
) -> PredictionResponse | None:
    """轨迹预测 — 基于 Mutual_Attention_opt 模型"""
    # 获取最近轨迹点
    query = text("""
        SELECT longitude, latitude, base_date_time
        FROM ais_raw
        WHERE mmsi = :mmsi AND longitude IS NOT NULL AND latitude IS NOT NULL
        ORDER BY base_date_time DESC
        LIMIT 120
    """)
    result = await db.execute(query, {"mmsi": mmsi})
    rows = result.fetchall()
    if len(rows) < 2:
        return None

    # 逆序为时间升序
    rows = list(reversed(rows))

    points = [
        ManualTrackPoint(lon=float(r.longitude), lat=float(r.latitude))
        for r in rows
    ]

    try:
        prediction = predict_from_manual_points(
            points=points,
            duration_minutes=duration_minutes,
            step_seconds=max(step_minutes * 60, 5),
        )
    except RuntimeError:
        return None

    prediction_data = prediction.model_dump()
    target_steps = max(duration_minutes // max(step_minutes, 1), 1)
    source_step_seconds = 30
    target_step_seconds = max(step_minutes * 60, source_step_seconds)
    sample_stride = max(target_step_seconds // source_step_seconds, 1)

    coords = prediction_data.get("predicted_track", {}).get("coordinates", [])
    times = prediction_data.get("predicted_timestamps", [])
    sampled_coords = coords[::sample_stride][:target_steps]
    sampled_times = times[::sample_stride][:target_steps]

    if isinstance(prediction_data.get("predicted_track"), dict):
        prediction_data["predicted_track"]["coordinates"] = sampled_coords
    prediction_data["predicted_timestamps"] = sampled_times
    prediction_data["mmsi"] = mmsi
    prediction_data["method"] = "mutual_attention_opt"
    return PredictionResponse(**prediction_data)
