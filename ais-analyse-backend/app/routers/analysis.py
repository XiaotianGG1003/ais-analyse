from datetime import datetime
import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.models.analysis import (
    AreaDetectionRequest,
    AreaDetectionResponse,
    DistanceResponse,
    ManualPredictionRequest,
    PredictionResponse,
    SimilarTracksRequest,
    TrackStatistics,
)
from app.services import analysis_service
from app.services.predictor_service import (
    find_similar_tracks_from_points,
    get_predictor_assets_status,
    prepare_predictor_assets,
    predict_from_manual_points,
)

router = APIRouter(prefix="/api", tags=["analysis"])
settings = get_settings()


class PredictorPrepareTaskState(BaseModel):
    task_id: str
    status: str
    stage: str
    progress: int
    message: str
    eta_seconds: int | None = None
    sample_count: int = 0
    ready: bool = False
    sample_pkl_exists: bool = False
    index_pkl_exists: bool = False
    sample_pkl_path: str | None = None
    index_pkl_path: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


_predictor_tasks: dict[str, PredictorPrepareTaskState] = {}
_predictor_tasks_lock = threading.Lock()
_predictor_executor = ThreadPoolExecutor(max_workers=1)
_predictor_task_started_at: dict[str, float] = {}


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _create_predictor_task() -> PredictorPrepareTaskState:
    now = _now_iso()
    task = PredictorPrepareTaskState(
        task_id=uuid.uuid4().hex,
        status="queued",
        stage="等待执行",
        progress=0,
        message="处理数据中",
        created_at=now,
        updated_at=now,
    )
    with _predictor_tasks_lock:
        _predictor_tasks[task.task_id] = task
    return task


def _update_predictor_task(task_id: str, **kwargs) -> PredictorPrepareTaskState:
    with _predictor_tasks_lock:
        task = _predictor_tasks[task_id]
        for key, value in kwargs.items():
            setattr(task, key, value)
        task.updated_at = _now_iso()
        return task


def _get_predictor_task(task_id: str) -> PredictorPrepareTaskState | None:
    with _predictor_tasks_lock:
        return _predictor_tasks.get(task_id)


def _get_running_predictor_task() -> PredictorPrepareTaskState | None:
    with _predictor_tasks_lock:
        for task in reversed(list(_predictor_tasks.values())):
            if task.status in {"queued", "running"}:
                return task
    return None


def _estimate_eta_seconds(start_time: float, completed_units: int, total_units: int) -> int | None:
    if completed_units <= 0 or total_units <= 0 or completed_units >= total_units:
        return None
    elapsed = time.time() - start_time
    if elapsed <= 0:
        return None
    rate = completed_units / elapsed
    if rate <= 0:
        return None
    remaining = total_units - completed_units
    return max(int(remaining / rate), 0)


def _run_predictor_prepare_sync(task_id: str):
    def _progress_cb(payload: dict):
        eta_seconds = None
        processed = int(payload.get("processed_vessels", 0))
        total = int(payload.get("total_vessels", 0))
        started_at = _predictor_task_started_at.get(task_id)
        if started_at is not None and total > 0:
            eta_seconds = _estimate_eta_seconds(started_at, processed, total)

        _update_predictor_task(
            task_id,
            stage=str(payload.get("stage", "processing_data")),
            progress=int(payload.get("progress", 0)),
            message=str(payload.get("message", "处理数据中")),
            eta_seconds=eta_seconds,
            sample_count=int(payload.get("sample_count", 0)),
        )

    result = prepare_predictor_assets(progress_callback=_progress_cb)
    _update_predictor_task(
        task_id,
        ready=bool(result.get("ready", False)),
        sample_pkl_exists=bool(result.get("sample_pkl_exists", False)),
        index_pkl_exists=bool(result.get("index_pkl_exists", False)),
        sample_pkl_path=result.get("sample_pkl_path"),
        index_pkl_path=result.get("index_pkl_path"),
        sample_count=int(result.get("sample_count", 0)),
    )


async def _run_predictor_prepare_task(task_id: str):
    try:
        _predictor_task_started_at[task_id] = time.time()
        _update_predictor_task(task_id, status="running", stage="processing_data", progress=1, message="处理数据中")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_predictor_executor, _run_predictor_prepare_sync, task_id)
        _update_predictor_task(
            task_id,
            status="completed",
            stage="completed",
            progress=100,
            message="处理完成，请绘制轨迹",
            eta_seconds=0,
        )
    except Exception as exc:
        _update_predictor_task(
            task_id,
            status="failed",
            stage="failed",
            progress=100,
            message="处理失败",
            eta_seconds=None,
            error=str(exc),
        )
    finally:
        _predictor_task_started_at.pop(task_id, None)


@router.get("/analysis/predictor-assets/status", response_model=dict)
async def predictor_assets_status():
    return {"code": 200, "data": get_predictor_assets_status()}


@router.post("/analysis/predictor-assets/prepare", response_model=dict)
async def prepare_predictor_assets_task():
    status = get_predictor_assets_status()
    if status.get("ready"):
        return {
            "code": 200,
            "data": {
                "ready": True,
                "status": "completed",
                "stage": "ready",
                "progress": 100,
                "message": "预测依赖文件已就绪",
                "eta_seconds": 0,
                **status,
            },
        }

    running_task = _get_running_predictor_task()
    if running_task is not None:
        return {
            "code": 202,
            "data": {
                "ready": False,
                "task_id": running_task.task_id,
                "status": running_task.status,
                "stage": running_task.stage,
                "progress": running_task.progress,
                "message": running_task.message,
                "eta_seconds": running_task.eta_seconds,
            },
        }

    task = _create_predictor_task()
    asyncio.create_task(_run_predictor_prepare_task(task.task_id))
    return {
        "code": 202,
        "data": {
            "ready": False,
            "task_id": task.task_id,
            "status": task.status,
            "stage": task.stage,
            "progress": task.progress,
            "message": task.message,
            "eta_seconds": task.eta_seconds,
        },
    }


@router.get("/analysis/predictor-assets/tasks/{task_id}", response_model=dict)
async def get_predictor_assets_task(task_id: str):
    task = _get_predictor_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"code": 200, "data": task.model_dump()}


@router.get("/vessels/{mmsi}/statistics", response_model=dict)
async def get_statistics(
    mmsi: int,
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """航行统计"""
    if mmsi < 100000000 or mmsi > 999999999:
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    delta = (end_time - start_time).days
    if delta > settings.max_query_days:
        raise HTTPException(
            status_code=400,
            detail=f"查询时间跨度不能超过 {settings.max_query_days} 天",
        )
    stats = await analysis_service.get_track_statistics(db, mmsi, start_time, end_time)
    if not stats:
        raise HTTPException(status_code=404, detail="未找到统计数据")
    return {"code": 200, "data": stats.model_dump()}


@router.post("/analysis/area-detection", response_model=dict)
async def area_detection(
    req: AreaDetectionRequest,
    db: AsyncSession = Depends(get_db),
):
    """区域检测 — 判断船舶是否进入指定区域"""
    delta = (req.end_time - req.start_time).days
    if delta > settings.max_query_days:
        raise HTTPException(
            status_code=400,
            detail=f"查询时间跨度不能超过 {settings.max_query_days} 天",
        )
    if req.area.get("type") != "Polygon":
        raise HTTPException(status_code=400, detail="区域必须是 GeoJSON Polygon 类型")
    result = await analysis_service.detect_area(db, req)
    return {"code": 200, "data": result.model_dump()}


@router.get("/analysis/distance", response_model=dict)
async def calc_distance(
    mmsi1: int = Query(..., ge=100000000, le=999999999),
    mmsi2: int = Query(..., ge=100000000, le=999999999),
    time: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """两船距离计算"""
    if mmsi1 == mmsi2:
        raise HTTPException(status_code=400, detail="两个 MMSI 不能相同")
    result = await analysis_service.calc_distance(db, mmsi1, mmsi2, time)
    if not result:
        raise HTTPException(status_code=404, detail="未找到船舶数据")
    return {"code": 200, "data": result.model_dump()}


@router.get("/vessels/{mmsi}/prediction", response_model=dict)
async def predict_trajectory(
    mmsi: int,
    duration_minutes: int = Query(
        default=60, ge=5, le=360, description="预测时长（分钟）"
    ),
    db: AsyncSession = Depends(get_db),
):
    """轨迹预测"""
    if mmsi < 100000000 or mmsi > 999999999:
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    result = await analysis_service.predict_trajectory(
        db,
        mmsi,
        duration_minutes,
        settings.prediction_step_minutes,
    )
    if not result:
        raise HTTPException(status_code=404, detail="轨迹点不足，无法预测")
    return {"code": 200, "data": result.model_dump()}


@router.post("/analysis/predict-manual", response_model=dict)
async def predict_manual_trajectory(req: ManualPredictionRequest):
    """手动点选轨迹预测：输入任意点，自动扩展到 120 点并补时间戳后进行预测。"""
    try:
        result = predict_from_manual_points(
            req.points,
            duration_minutes=req.duration_minutes,
            step_seconds=req.step_seconds,
        )
        return {"code": 200, "data": result.model_dump()}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/analysis/similar-tracks", response_model=dict)
async def similar_tracks(req: SimilarTracksRequest):
    """手动点选轨迹相似检索：返回最相似 Top-K 轨迹。"""
    try:
        result = find_similar_tracks_from_points(req.points, req.top_k)
        return {"code": 200, "data": result.model_dump()}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
