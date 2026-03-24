from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anomaly import (
    AnomalyDetectionRequest,
    AnomalyDetectionResponse,
    AnomalyEvent,
)


@dataclass
class _TrackPoint:
    ts: datetime
    lon: float
    lat: float
    sog: float
    cog: float | None


def _to_iso(ts: datetime) -> str:
    return ts.isoformat()


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    r = 6371000.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _normalize_time(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.replace(tzinfo=None)


def _angle_diff_deg(a: float, b: float) -> float:
    diff = abs((a - b + 180.0) % 360.0 - 180.0)
    return diff


def _severity_by_score(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


async def _load_track_points(
    db: AsyncSession,
    mmsi: int,
    start_time: datetime,
    end_time: datetime,
) -> list[_TrackPoint]:
    query = text(
        """
        SELECT base_date_time, longitude, latitude, sog, cog
        FROM ais_raw
        WHERE mmsi = :mmsi
          AND base_date_time >= :t_start
          AND base_date_time <= :t_end
          AND longitude IS NOT NULL
          AND latitude IS NOT NULL
        ORDER BY base_date_time
        """
    )
    result = await db.execute(
        query,
        {
            "mmsi": mmsi,
            "t_start": _normalize_time(start_time),
            "t_end": _normalize_time(end_time),
        },
    )

    points: list[_TrackPoint] = []
    for row in result.fetchall():
        points.append(
            _TrackPoint(
                ts=row.base_date_time,
                lon=float(row.longitude),
                lat=float(row.latitude),
                sog=float(row.sog or 0.0),
                cog=float(row.cog) if row.cog is not None else None,
            )
        )
    return points


async def _detect_forbidden_area(
    db: AsyncSession,
    req: AnomalyDetectionRequest,
) -> list[AnomalyEvent]:
    def _extract_forbidden_areas(request: AnomalyDetectionRequest) -> list[tuple[str, dict]]:
        areas: list[tuple[str, dict]] = []

        if request.forbidden_areas:
            for idx, item in enumerate(request.forbidden_areas, start=1):
                area_name = f"禁区{idx}"
                area_geometry: dict | None = None

                if isinstance(item, dict):
                    if item.get("type") == "Polygon":
                        area_geometry = item
                    elif isinstance(item.get("geometry"), dict) and item["geometry"].get("type") == "Polygon":
                        area_geometry = item["geometry"]
                        if isinstance(item.get("name"), str) and item["name"].strip():
                            area_name = item["name"].strip()
                else:
                    geometry = getattr(item, "geometry", None)
                    name = getattr(item, "name", None)
                    if isinstance(geometry, dict) and geometry.get("type") == "Polygon":
                        area_geometry = geometry
                        if isinstance(name, str) and name.strip():
                            area_name = name.strip()

                if area_geometry is not None:
                    areas.append((area_name, area_geometry))

        if not areas and request.forbidden_area is not None and request.forbidden_area.get("type") == "Polygon":
            areas.append(("禁区1", request.forbidden_area))

        return areas

    input_areas = _extract_forbidden_areas(req)
    if not input_areas:
        return []

    events: list[AnomalyEvent] = []

    query = text(
        """
        WITH inside_pts AS (
            SELECT base_date_time, longitude, latitude
            FROM ais_raw
            WHERE mmsi = :mmsi
              AND base_date_time >= :t_start
              AND base_date_time <= :t_end
              AND longitude IS NOT NULL
              AND latitude IS NOT NULL
              AND ST_Contains(
                  ST_SetSRID(ST_GeomFromGeoJSON(:area_json), 4326),
                  ST_SetSRID(ST_Point(longitude, latitude), 4326)
              )
            ORDER BY base_date_time
        )
        SELECT
            COUNT(*) AS point_count,
            MIN(base_date_time) AS enter_time,
            MAX(base_date_time) AS exit_time,
            AVG(longitude) AS center_lon,
            AVG(latitude) AS center_lat
        FROM inside_pts
        """
    )
    for idx, (area_name, area_geometry) in enumerate(input_areas, start=1):
        result = await db.execute(
            query,
            {
                "mmsi": req.mmsi,
                "t_start": _normalize_time(req.start_time),
                "t_end": _normalize_time(req.end_time),
                "area_json": json.dumps(area_geometry),
            },
        )
        row = result.fetchone()
        if not row or int(row.point_count or 0) == 0:
            continue

        score = min(0.6 + float(row.point_count) / 200.0, 1.0)
        events.append(
            AnomalyEvent(
                event_id=f"forbidden-area-{idx}",
                event_type="forbidden_area_entry",
                severity=_severity_by_score(score),
                score=round(score, 3),
                start_time=_to_iso(row.enter_time),
                end_time=_to_iso(row.exit_time),
                position={"lon": float(row.center_lon), "lat": float(row.center_lat)},
                forbidden_area_name=area_name,
                evidence={
                    "inside_point_count": int(row.point_count),
                    "rule": "inside_forbidden_area",
                    "forbidden_area_name": area_name,
                },
            )
        )

    return events


def _detect_speed_anomalies(points: list[_TrackPoint], speed_threshold_knots: float) -> list[AnomalyEvent]:
    events: list[AnomalyEvent] = []
    for idx, p in enumerate(points, start=1):
        if p.sog < speed_threshold_knots:
            continue
        overspeed = p.sog - speed_threshold_knots
        score = min(0.4 + overspeed / 20.0, 1.0)
        events.append(
            AnomalyEvent(
                event_id=f"overspeed-{idx}",
                event_type="overspeed",
                severity=_severity_by_score(score),
                score=round(score, 3),
                start_time=_to_iso(p.ts),
                end_time=_to_iso(p.ts),
                position={"lon": p.lon, "lat": p.lat},
                evidence={
                    "sog_knots": round(p.sog, 2),
                    "threshold_knots": speed_threshold_knots,
                },
            )
        )
    return events


def _detect_turn_anomalies(
    points: list[_TrackPoint],
    turn_rate_threshold_deg_per_min: float,
) -> list[AnomalyEvent]:
    events: list[AnomalyEvent] = []
    for i in range(1, len(points)):
        prev_p = points[i - 1]
        curr_p = points[i]
        if prev_p.cog is None or curr_p.cog is None:
            continue

        dt_min = (curr_p.ts - prev_p.ts).total_seconds() / 60.0
        if dt_min <= 0:
            continue

        # 低速时航向变化噪声较大，跳过
        if (prev_p.sog + curr_p.sog) / 2.0 < 3.0:
            continue

        angle_delta = _angle_diff_deg(curr_p.cog, prev_p.cog)
        turn_rate = angle_delta / dt_min
        if turn_rate < turn_rate_threshold_deg_per_min:
            continue

        score = min(0.45 + (turn_rate - turn_rate_threshold_deg_per_min) / 60.0, 1.0)
        events.append(
            AnomalyEvent(
                event_id=f"sharp-turn-{i}",
                event_type="sharp_turn",
                severity=_severity_by_score(score),
                score=round(score, 3),
                start_time=_to_iso(prev_p.ts),
                end_time=_to_iso(curr_p.ts),
                position={"lon": curr_p.lon, "lat": curr_p.lat},
                evidence={
                    "turn_rate_deg_per_min": round(turn_rate, 2),
                    "threshold_deg_per_min": turn_rate_threshold_deg_per_min,
                    "heading_change_deg": round(angle_delta, 2),
                },
            )
        )
    return events


def _detect_stop_anomalies(
    points: list[_TrackPoint],
    stop_speed_threshold_knots: float,
    stop_min_minutes: int,
    stop_radius_m: float,
) -> list[AnomalyEvent]:
    events: list[AnomalyEvent] = []
    i = 0
    n = len(points)

    while i < n:
        if points[i].sog > stop_speed_threshold_knots:
            i += 1
            continue

        start = i
        anchor = points[i]
        j = i + 1
        while j < n:
            p = points[j]
            if p.sog > stop_speed_threshold_knots:
                break

            # 超过漂移半径，认为不是同一停留片段
            drift_m = _haversine_m(anchor.lon, anchor.lat, p.lon, p.lat)
            if drift_m > stop_radius_m:
                break

            # 时间间隔过大，不属于连续停留
            gap_minutes = (p.ts - points[j - 1].ts).total_seconds() / 60.0
            if gap_minutes > 30:
                break

            j += 1

        end = j - 1
        if end > start:
            duration_minutes = (points[end].ts - points[start].ts).total_seconds() / 60.0
            if duration_minutes >= stop_min_minutes:
                score = min(0.5 + duration_minutes / 240.0, 1.0)
                center_lon = sum(p.lon for p in points[start : end + 1]) / (end - start + 1)
                center_lat = sum(p.lat for p in points[start : end + 1]) / (end - start + 1)
                events.append(
                    AnomalyEvent(
                        event_id=f"abnormal-stop-{start}",
                        event_type="abnormal_stop",
                        severity=_severity_by_score(score),
                        score=round(score, 3),
                        start_time=_to_iso(points[start].ts),
                        end_time=_to_iso(points[end].ts),
                        position={"lon": center_lon, "lat": center_lat},
                        evidence={
                            "duration_minutes": round(duration_minutes, 1),
                            "stop_speed_threshold_knots": stop_speed_threshold_knots,
                            "stop_radius_m": stop_radius_m,
                            "point_count": end - start + 1,
                        },
                    )
                )

        i = max(j, i + 1)

    return events


def _build_summary(events: list[AnomalyEvent]) -> tuple[dict[str, int], dict[str, int]]:
    severity_count = {"high": 0, "medium": 0, "low": 0}
    type_count: dict[str, int] = {}

    for ev in events:
        severity_count[ev.severity] = severity_count.get(ev.severity, 0) + 1
        type_count[ev.event_type] = type_count.get(ev.event_type, 0) + 1

    return severity_count, type_count


async def detect_priority_anomalies(
    db: AsyncSession,
    req: AnomalyDetectionRequest,
) -> AnomalyDetectionResponse:
    points = await _load_track_points(db, req.mmsi, req.start_time, req.end_time)
    if len(points) < 2:
        return AnomalyDetectionResponse(
            mmsi=req.mmsi,
            start_time=_to_iso(req.start_time),
            end_time=_to_iso(req.end_time),
            event_count=0,
            severity_count={"high": 0, "medium": 0, "low": 0},
            type_count={},
            events=[],
        )

    events: list[AnomalyEvent] = []
    events.extend(_detect_speed_anomalies(points, req.speed_threshold_knots))
    events.extend(_detect_turn_anomalies(points, req.turn_rate_threshold_deg_per_min))
    events.extend(
        _detect_stop_anomalies(
            points,
            stop_speed_threshold_knots=req.stop_speed_threshold_knots,
            stop_min_minutes=req.stop_min_minutes,
            stop_radius_m=req.stop_radius_m,
        )
    )

    events.extend(await _detect_forbidden_area(db, req))

    events.sort(key=lambda x: (x.start_time, -x.score))
    severity_count, type_count = _build_summary(events)

    return AnomalyDetectionResponse(
        mmsi=req.mmsi,
        start_time=_to_iso(req.start_time),
        end_time=_to_iso(req.end_time),
        event_count=len(events),
        severity_count=severity_count,
        type_count=type_count,
        events=events,
    )
