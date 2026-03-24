from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.anomaly import AnomalyDetectionRequest
from app.services.anomaly_service import detect_priority_anomalies

router = APIRouter(prefix="/api/anomalies", tags=["anomalies"])


@router.post("/detect", response_model=dict)
async def detect_anomalies(
    req: AnomalyDetectionRequest,
    db: AsyncSession = Depends(get_db),
):
    if req.end_time <= req.start_time:
        raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")

    if req.forbidden_area is not None and req.forbidden_area.get("type") != "Polygon":
        raise HTTPException(status_code=400, detail="forbidden_area 必须是 GeoJSON Polygon")

    if req.forbidden_areas:
        for idx, item in enumerate(req.forbidden_areas, start=1):
            if isinstance(item, dict):
                if item.get("type") == "Polygon":
                    continue
                geometry = item.get("geometry")
                if isinstance(geometry, dict) and geometry.get("type") == "Polygon":
                    continue
            else:
                geometry = getattr(item, "geometry", None)
                if isinstance(geometry, dict) and geometry.get("type") == "Polygon":
                    continue

            raise HTTPException(
                status_code=400,
                detail=f"forbidden_areas[{idx}] 必须是 Polygon 或 {{name, geometry: Polygon}}",
            )

    result = await detect_priority_anomalies(db, req)
    return {"code": 200, "data": result.model_dump()}
