from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.port import PortCreateRequest
from app.services import port_service

router = APIRouter(prefix="/api/ports", tags=["ports"])
settings = get_settings()


@router.get("", response_model=dict)
async def list_ports(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None, min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db),
):
    page_size = min(page_size, settings.max_page_size)
    result = await port_service.list_ports(db, page=page, page_size=page_size, keyword=keyword)
    return {"code": 200, "data": result.model_dump()}


@router.post("", response_model=dict)
async def create_port(
    req: PortCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    bbox = req.bbox
    if bbox.min_lon >= bbox.max_lon or bbox.min_lat >= bbox.max_lat:
        raise HTTPException(status_code=400, detail="矩形区域无效，最小坐标必须小于最大坐标")

    item = await port_service.create_port(
        db=db,
        name=req.name.strip(),
        min_lon=bbox.min_lon,
        min_lat=bbox.min_lat,
        max_lon=bbox.max_lon,
        max_lat=bbox.max_lat,
    )
    return {"code": 200, "data": item.model_dump()}


@router.delete("/{port_id}", response_model=dict)
async def delete_port(
    port_id: int,
    db: AsyncSession = Depends(get_db),
):
    ok = await port_service.delete_port(db, port_id)
    if not ok:
        raise HTTPException(status_code=404, detail="港口不存在")
    return {"code": 200, "data": {"deleted": True, "port_id": port_id}}


@router.get("/{port_id}/analysis", response_model=dict)
async def get_port_analysis(
    port_id: int,
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    top_n: int = Query(default=5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    if start_time >= end_time:
        raise HTTPException(status_code=400, detail="起始时间必须早于结束时间")

    delta = (end_time - start_time).days
    if delta > settings.max_query_days:
        raise HTTPException(
            status_code=400,
            detail=f"查询时间跨度不能超过 {settings.max_query_days} 天",
        )

    result = await port_service.get_port_analysis(
        db,
        port_id=port_id,
        start_time=start_time,
        end_time=end_time,
        top_n=top_n,
    )
    if not result:
        raise HTTPException(status_code=404, detail="港口不存在")
    return {"code": 200, "data": result.model_dump()}
