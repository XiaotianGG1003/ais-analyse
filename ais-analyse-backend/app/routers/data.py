"""
数据导入路由：CSV 文件 → ais_segments (MobilityDB tgeompoint) + ais_raw (平铺表)
调用 data_process.import_csv_to_mobilitydb 实现核心导入逻辑。
"""
import asyncio
import csv
import logging
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
import psycopg2
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session, get_db


def _get_import_func():
    """延迟导入，避免 data_process 顶层依赖（scipy 等）未安装时影响 API 启动。"""
    try:
        from app.data_process import import_csv_to_mobilitydb  # noqa: PLC0415
        return import_csv_to_mobilitydb
    except ImportError as exc:
        raise RuntimeError(
            f"data_process 模块加载失败，请确认已安装 psycopg2、scipy、pandas：{exc}"
        ) from exc

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api/data", tags=["data"])

# 单线程执行器，避免多文件并发导入造成数据库锁
_executor = ThreadPoolExecutor(max_workers=1)

# ── 列名别名映射（CSV 原始列名 → ais_raw 列名）────────────────────────────
_COL_ALIASES: dict[str, str] = {
    "mmsi": "mmsi",
    "basedatetime": "base_date_time",
    "base_date_time": "base_date_time",
    "timestamp": "base_date_time",
    "lat": "latitude",
    "latitude": "latitude",
    "lon": "longitude",
    "long": "longitude",
    "longitude": "longitude",
    "sog": "sog",
    "cog": "cog",
    "heading": "heading",
    "vesselname": "vessel_name",
    "vessel_name": "vessel_name",
    "name": "vessel_name",
    "imo": "imo",
    "callsign": "call_sign",
    "call_sign": "call_sign",
    "vesseltype": "vessel_type",
    "vessel_type": "vessel_type",
    "status": "status",
    "length": "length",
    "width": "width",
    "draft": "draft",
    "cargo": "cargo",
    "transcieverclass": "transceiver",
    "transceiver": "transceiver",
}

_ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ais_raw (
    mmsi BIGINT,
    base_date_time TIMESTAMP,
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    sog DOUBLE PRECISION,
    cog DOUBLE PRECISION,
    heading DOUBLE PRECISION,
    vessel_name TEXT,
    imo TEXT,
    call_sign TEXT,
    vessel_type INT,
    status INT,
    length DOUBLE PRECISION,
    width DOUBLE PRECISION,
    draft DOUBLE PRECISION,
    cargo INT,
    transceiver TEXT
);
CREATE INDEX IF NOT EXISTS idx_ais_raw_mmsi ON ais_raw(mmsi);
CREATE INDEX IF NOT EXISTS idx_ais_raw_time ON ais_raw(base_date_time);
CREATE INDEX IF NOT EXISTS idx_ais_raw_mmsi_time ON ais_raw(mmsi, base_date_time);
"""

_MAX_UPLOAD_SIZE = 500 * 1024 * 1024
_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


class ImportPathRequest(BaseModel):
    file_path: str
    rebuild_trips: bool = True


class ImportTaskState(BaseModel):
    task_id: str
    status: str
    stage: str
    progress: int
    source: str
    filename: str | None = None
    total_rows: int = 0
    current_rows: int = 0
    rows_inserted: int = 0
    segments_inserted: int = 0
    eta_seconds: int | None = None
    mobility_status: str = "queued"
    mobility_stage: str = "等待执行"
    mobility_progress: int = 0
    mobility_total_rows: int = 0
    mobility_current_rows: int = 0
    mobility_eta_seconds: int | None = None
    pkl_status: str = "queued"
    pkl_stage: str = "等待开始"
    pkl_progress: int = 0
    pkl_eta_seconds: int | None = None
    pkl_sample_count: int = 0
    pkl_output_path: str | None = None
    trips_rebuilt: bool = False
    error: str | None = None
    created_at: str
    updated_at: str


_import_tasks: dict[str, ImportTaskState] = {}
_tasks_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _create_task_state(source: str, filename: str | None = None) -> ImportTaskState:
    now = _now_iso()
    task = ImportTaskState(
        task_id=uuid.uuid4().hex,
        status="queued",
        stage="等待执行",
        progress=0,
        source=source,
        filename=filename,
        created_at=now,
        updated_at=now,
    )
    with _tasks_lock:
        _import_tasks[task.task_id] = task
    return task


def _update_task_state(task_id: str, **kwargs) -> ImportTaskState:
    with _tasks_lock:
        task = _import_tasks[task_id]
        for key, value in kwargs.items():
            setattr(task, key, value)
        task.updated_at = _now_iso()
        return task


def _get_task_state(task_id: str) -> ImportTaskState | None:
    with _tasks_lock:
        return _import_tasks.get(task_id)


def _list_task_states(limit: int = 10) -> list[ImportTaskState]:
    with _tasks_lock:
        tasks = list(_import_tasks.values())
    tasks.sort(key=lambda task: (task.updated_at, task.created_at), reverse=True)
    return tasks[:limit]


def _count_csv_rows_sync(csv_path: str) -> int:
    row_count = 0
    with open(csv_path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            row_count += chunk.count(b"\n")
    return max(row_count - 1, 0)


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


def _make_progress_callback(task_id: str, phase: str, total_rows: int, start_time: float):
    phase_base = 10 if phase == "segments" else 60
    phase_span = 45 if phase == "segments" else 30
    work_offset = 0 if phase == "segments" else total_rows
    total_work = max(total_rows * 2, 1)

    def _callback(payload: dict):
        rows_read = int(payload.get("rows_read", 0))
        subphase = payload.get("subphase")

        if phase == "segments" and subphase == "inserting":
            total_mmsi = int(payload.get("total_mmsi", 0))
            processed_mmsi = int(payload.get("processed_mmsi", 0))
            insert_ratio = min(processed_mmsi / total_mmsi, 1.0) if total_mmsi > 0 else 0.0
            ratio = insert_ratio
        else:
            ratio = min(rows_read / total_rows, 1.0) if total_rows > 0 else 0.0

        if phase == "segments":
            completed_units = min(int(ratio * total_rows), total_rows)
            total_units = max(total_rows, 1)
        else:
            completed_units = min(work_offset + rows_read, total_work)
            total_units = total_work

        eta_seconds = _estimate_eta_seconds(start_time, completed_units, total_units)
        if phase == "segments":
            if subphase == "inserting":
                # 写入阶段独立区间：40~55，避免从读取阶段回退
                progress = 40 + int(ratio * 15)
            else:
                # 读取阶段区间：10~40
                progress = 10 + int(ratio * 30)
        else:
            progress_delta = int(ratio * phase_span)
            if rows_read > 0 and ratio < 1.0:
                progress_delta = max(progress_delta, 1)
            progress = phase_base + progress_delta

        current_task = _get_task_state(task_id)
        current_mobility_progress = current_task.mobility_progress if current_task else 0
        progress = max(progress, current_mobility_progress)

        stage_text = None
        if phase == "segments" and subphase == "inserting":
            stage_text = "导入 ais_segments（写入阶段）"

        update_kwargs = {
            "current_rows": rows_read,
            "total_rows": total_rows,
            "progress": min(progress, 90),
            "eta_seconds": eta_seconds,
            "mobility_current_rows": rows_read,
            "mobility_total_rows": total_rows,
            "mobility_progress": min(progress, 90),
            "mobility_eta_seconds": eta_seconds,
        }
        if stage_text:
            update_kwargs["mobility_stage"] = stage_text

        _update_task_state(
            task_id,
            **update_kwargs,
        )

    return _callback


def _get_conn_params() -> dict:
    """从 DATABASE_URL 解析 psycopg2 连接参数。"""
    settings = get_settings()
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    p = urlparse(url)
    return {
        "host": p.hostname,
        "port": p.port or 5432,
        "dbname": p.path.lstrip("/"),
        "user": p.username,
        "password": p.password or "",
    }


def _to_float_list(series: pd.Series) -> list:
    return [None if pd.isna(v) else float(v) for v in series]


def _to_int_list(series: pd.Series) -> list:
    return [None if pd.isna(v) else int(v) for v in series]


def _to_str_list(series: pd.Series) -> list:
    return [None if (pd.isna(v) or str(v).strip() in ("", "nan", "None")) else str(v).strip() for v in series]


def _to_dt_list(series: pd.Series) -> list:
    return [None if pd.isna(v) else v.to_pydatetime() for v in series]


def _import_to_ais_raw(csv_path: str, conn_params: dict, progress_callback=None) -> int:
    """
    将 CSV 批量插入 ais_raw 表（供 Web 端 vessels API 使用）。
    返回插入行数。
    """
    conn = psycopg2.connect(**conn_params)
    cur = conn.cursor()

    # 确保表存在（幂等）
    for stmt in _ENSURE_TABLE_SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            cur.execute(s)
    conn.commit()

    # 首次迁移时才执行全表去重 + 唯一索引创建，避免每次导入做重扫描
    cur.execute("SELECT to_regclass('public.idx_ais_raw_mmsi_time_uniq')")
    uniq_idx = cur.fetchone()[0]
    if uniq_idx is None:
        logger.info("ais_raw 首次执行去重并建立唯一索引（mmsi, base_date_time）")
        cur.execute(
            """
            DELETE FROM ais_raw a
            USING ais_raw b
            WHERE a.ctid < b.ctid
              AND a.mmsi = b.mmsi
              AND a.base_date_time = b.base_date_time
            """
        )
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ais_raw_mmsi_time_uniq ON ais_raw(mmsi, base_date_time)")
        conn.commit()

    def _norm_col(name: str) -> str:
        return name.strip().lower().replace(" ", "").replace("-", "").replace("_", "")

    def _quote_ident(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    type_cast = {
        "mmsi": "BIGINT",
        "base_date_time": "TIMESTAMP",
        "longitude": "DOUBLE PRECISION",
        "latitude": "DOUBLE PRECISION",
        "sog": "DOUBLE PRECISION",
        "cog": "DOUBLE PRECISION",
        "heading": "DOUBLE PRECISION",
        "vessel_name": "TEXT",
        "imo": "TEXT",
        "call_sign": "TEXT",
        "vessel_type": "INT",
        "status": "INT",
        "length": "DOUBLE PRECISION",
        "width": "DOUBLE PRECISION",
        "draft": "DOUBLE PRECISION",
        "cargo": "INT",
        "transceiver": "TEXT",
    }

    pg_type_to_cast = {
        "bigint": "BIGINT",
        "integer": "INT",
        "real": "REAL",
        "double precision": "DOUBLE PRECISION",
        "text": "TEXT",
        "character varying": "TEXT",
        "timestamp without time zone": "TIMESTAMP",
    }

    cur.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ais_raw'
        """
    )
    for col_name, data_type in cur.fetchall():
        if col_name in type_cast and data_type in pg_type_to_cast:
            type_cast[col_name] = pg_type_to_cast[data_type]

    target_cols = [
        "mmsi", "base_date_time", "longitude", "latitude", "sog", "cog",
        "heading", "vessel_name", "imo", "call_sign", "vessel_type", "status",
        "length", "width", "draft", "cargo", "transceiver",
    ]

    header_line = None
    used_encoding = "utf-8-sig"
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(csv_path, "r", encoding=enc, newline="") as f:
                header_line = f.readline()
            used_encoding = enc
            break
        except UnicodeDecodeError:
            continue

    if not header_line:
        cur.close()
        conn.close()
        raise RuntimeError("CSV 文件为空或编码不支持")

    csv_reader = csv.reader([header_line])
    raw_headers = next(csv_reader)
    if not raw_headers:
        cur.close()
        conn.close()
        raise RuntimeError("CSV 缺少标题行")

    temp_cols = [f"col_{i}" for i in range(len(raw_headers))]
    temp_table = f"tmp_ais_raw_import_{uuid.uuid4().hex[:8]}"
    cur.execute(
        f"CREATE TEMP TABLE {temp_table} ("
        + ", ".join([f"{_quote_ident(c)} TEXT" for c in temp_cols])
        + ") ON COMMIT DROP"
    )

    copy_sql = (
        f"COPY {temp_table} ("
        + ", ".join([_quote_ident(c) for c in temp_cols])
        + ") FROM STDIN WITH (FORMAT csv, HEADER true)"
    )

    total_size = max(os.path.getsize(csv_path), 1)
    total_rows_hint = _count_csv_rows_sync(csv_path)

    class _ProgressFile:
        def __init__(self, fp, size):
            self.fp = fp
            self.size = size
            self.last_report = 0.0

        def read(self, n=-1):
            data = self.fp.read(n)
            if data and progress_callback:
                now_ratio = min(self.fp.tell() / self.size, 1.0)
                if now_ratio - self.last_report >= 0.01 or now_ratio >= 1.0:
                    est_rows = int(now_ratio * total_rows_hint)
                    progress_callback(
                        {
                            "phase": "segments",
                            "rows_read": est_rows,
                            "rows_inserted": 0,
                            "subphase": "copying_raw",
                        }
                    )
                    self.last_report = now_ratio
            return data

    with open(csv_path, "r", encoding=used_encoding, newline="") as f:
        wrapped = _ProgressFile(f, total_size)
        cur.copy_expert(copy_sql, wrapped)

    header_to_db: dict[str, str] = {}
    for idx, raw_h in enumerate(raw_headers):
        norm = _norm_col(raw_h)
        db_col = _COL_ALIASES.get(norm) or _COL_ALIASES.get(raw_h.strip().lower())
        if db_col and db_col not in header_to_db:
            header_to_db[db_col] = temp_cols[idx]

    select_exprs = []
    for col in target_cols:
        src_col = header_to_db.get(col)
        if src_col:
            if type_cast[col] == "TEXT":
                expr = f"NULLIF(TRIM({_quote_ident(src_col)}), '')::{type_cast[col]}"
            else:
                expr = f"NULLIF(TRIM({_quote_ident(src_col)}), '')::{type_cast[col]}"
        else:
            expr = f"NULL::{type_cast[col]}"
        select_exprs.append(expr)

    cur.execute(
        "INSERT INTO ais_raw ("
        + ", ".join(target_cols)
        + ") SELECT "
        + ", ".join(select_exprs)
        + f" FROM {temp_table} ON CONFLICT (mmsi, base_date_time) DO NOTHING"
    )
    inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    conn.commit()

    if progress_callback:
        progress_callback(
            {
                "phase": "segments",
                "rows_read": total_rows_hint,
                "rows_inserted": inserted,
                "subphase": "copying_raw",
            }
        )

    cur.close()
    conn.close()
    return inserted


def _run_import_sync(csv_path: str, conn_params: dict) -> dict:
    """
    同步执行完整导入流水线：
      1. import_csv_to_mobilitydb → ais_segments (tgeompoint)
      2. _import_to_ais_raw → ais_raw (平铺表)
    """
    import_csv_to_mobilitydb = _get_import_func()
    logger.info("开始导入 ais_segments (MobilityDB tgeompoint)...")
    segments = import_csv_to_mobilitydb(csv_path, conn_params=conn_params)
    logger.info(f"ais_segments 插入完成：{segments} 个轨迹段")

    logger.info("开始导入 ais_raw...")
    rows = _import_to_ais_raw(csv_path, conn_params)
    logger.info(f"ais_raw 插入完成：{rows} 行")

    return {"segments": segments, "rows": rows}


def _run_segments_import_sync(csv_path: str, conn_params: dict, progress_callback=None) -> int:
    import_csv_to_mobilitydb = _get_import_func()
    logger.info("开始导入 ais_segments (MobilityDB tgeompoint)...")
    segments = import_csv_to_mobilitydb(
        csv_path,
        conn_params=conn_params,
        progress_callback=progress_callback,
    )
    logger.info(f"ais_segments 插入完成：{segments} 个轨迹段")
    return segments


def _run_ais_raw_import_sync(csv_path: str, conn_params: dict, progress_callback=None) -> int:
    logger.info("开始导入 ais_raw...")
    rows = _import_to_ais_raw(csv_path, conn_params, progress_callback=progress_callback)
    logger.info(f"ais_raw 插入完成：{rows} 行")
    return rows


def _get_generate_pkl_func():
    try:
        from app.data_process import generate_samples_pkl_from_ais_raw  # noqa: PLC0415
        return generate_samples_pkl_from_ais_raw
    except ImportError as exc:
        raise RuntimeError(
            f"data_process 模块加载失败，请确认已安装 psycopg2、scipy、pandas：{exc}"
        ) from exc


def _make_pkl_progress_callback(task_id: str, start_time: float):
    def _callback(payload: dict):
        processed = int(payload.get("processed_vessels", 0))
        total = int(payload.get("total_vessels", 0))
        progress = int((processed / total) * 100) if total > 0 else 0
        eta_seconds = _estimate_eta_seconds(start_time, processed, total) if total > 0 else None
        _update_task_state(
            task_id,
            pkl_stage="轨迹处理并生成 pkl",
            pkl_progress=min(progress, 100),
            pkl_eta_seconds=eta_seconds,
            pkl_sample_count=int(payload.get("sample_count", 0)),
            pkl_output_path=payload.get("out_pkl"),
        )

    return _callback


def _run_generate_pkl_sync(task_id: str, conn_params: dict):
    generate_samples_pkl = _get_generate_pkl_func()
    out_pkl = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "samples", "global_traj_id.pkl"))
    start_time = time.time()
    result = generate_samples_pkl(
        out_pkl=out_pkl,
        conn_params=conn_params,
        table="ais_raw",
        progress_callback=_make_pkl_progress_callback(task_id, start_time),
    )
    return result


async def _rebuild_vessels_table(db: AsyncSession, rows_inserted: int, rebuild_trips: bool) -> bool:
    trips_rebuilt = False
    if rebuild_trips and rows_inserted > 0:
        try:
            await db.execute(text("SELECT build_vessel_trips()"))
            await db.commit()
            trips_rebuilt = True
            logger.info("vessels 表重建完成")
        except Exception as exc:
            logger.warning(f"vessels 表重建失败（数据已导入）: {exc}")
    return trips_rebuilt


async def _run_import_task(
    task_id: str,
    csv_path: str,
    source: str,
    rebuild_trips: bool,
    delete_after: bool = False,
):
    conn_params = _get_conn_params()
    start_time = time.time()
    try:
        _update_task_state(
            task_id,
            status="running",
            stage="导入 MobilityDB",
            progress=5,
            mobility_status="running",
            mobility_stage="统计总行数",
            pkl_status="queued",
            pkl_stage="等待导入完成",
        )
        loop = asyncio.get_running_loop()
        total_rows: int = await loop.run_in_executor(_executor, _count_csv_rows_sync, csv_path)
        _update_task_state(task_id, total_rows=total_rows, mobility_total_rows=total_rows)

        _update_task_state(task_id, stage="导入 MobilityDB", progress=10, current_rows=0, mobility_stage="复制 CSV 到 ais_raw")
        rows: int = await loop.run_in_executor(
            _executor,
            _run_ais_raw_import_sync,
            csv_path,
            conn_params,
            _make_progress_callback(task_id, "segments", total_rows, start_time),
        )

        _update_task_state(
            task_id,
            stage="导入 MobilityDB",
            progress=90,
            current_rows=total_rows,
            rows_inserted=rows,
            segments_inserted=0,
            eta_seconds=None,
            mobility_stage="复制完成",
            mobility_eta_seconds=None,
        )

        trips_rebuilt = False

        _update_task_state(
            task_id,
            stage="导入 MobilityDB",
            progress=100,
            mobility_status="completed",
            mobility_stage="导入完成",
            mobility_progress=100,
            mobility_current_rows=total_rows,
            mobility_total_rows=total_rows,
            mobility_eta_seconds=0,
        )

        _update_task_state(
            task_id,
            stage="轨迹处理并生成 pkl",
            pkl_status="running",
            pkl_stage="读取轨迹并处理",
            pkl_progress=5,
        )

        pkl_result: dict = await loop.run_in_executor(
            _executor,
            _run_generate_pkl_sync,
            task_id,
            conn_params,
        )

        _update_task_state(
            task_id,
            status="completed",
            stage="导入完成",
            progress=100,
            current_rows=total_rows,
            rows_inserted=rows,
            segments_inserted=0,
            eta_seconds=0,
            trips_rebuilt=trips_rebuilt,
            pkl_status="completed",
            pkl_stage="生成完成",
            pkl_progress=100,
            pkl_eta_seconds=0,
            pkl_sample_count=int(pkl_result.get("sample_count", 0)),
            pkl_output_path=pkl_result.get("out_pkl"),
        )
    except Exception as exc:
        logger.error(f"导入任务失败 [{task_id}]: {exc}")
        _update_task_state(
            task_id,
            status="failed",
            stage="导入失败",
            progress=100,
            error=str(exc),
            pkl_status="failed",
            pkl_stage="处理失败",
        )
    finally:
        if delete_after and os.path.exists(csv_path):
            os.unlink(csv_path)


@router.post("/import-csv")
async def import_csv(
    file: UploadFile = File(...),
    rebuild_trips: bool = Query(default=True, description="导入后重建 MobilityDB vessels 轨迹表"),
):
    """
    上传 AIS CSV 文件并导入数据库。
    同时填充 ais_segments（MobilityDB tgeompoint，供轨迹预测使用）
    和 ais_raw（供 Web 端 vessels API 使用），并可选重建 vessels 聚合表。
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="请上传 .csv 格式文件")

    # 写入临时文件（psycopg2 / pandas 需要文件路径）
    total_size = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        tmp_path = tmp.name
        while True:
            chunk = await file.read(_UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > _MAX_UPLOAD_SIZE:
                tmp.close()
                os.unlink(tmp_path)
                raise HTTPException(
                    status_code=413,
                    detail="浏览器上传仅支持 500MB 以内文件。7GB 这类大文件请使用“按路径导入”。",
                )
            tmp.write(chunk)

    task = _create_task_state(source="upload", filename=file.filename)
    asyncio.create_task(
        _run_import_task(task.task_id, tmp_path, "upload", rebuild_trips, delete_after=True)
    )

    return {
        "code": 202,
        "data": {
            "task_id": task.task_id,
            "status": task.status,
            "stage": task.stage,
            "progress": task.progress,
        },
    }


@router.post("/import-path")
async def import_csv_by_path(
    req: ImportPathRequest,
):
    """按服务端本地路径导入 CSV，适合数 GB 的大文件。"""
    csv_path = os.path.abspath(req.file_path.strip())
    if not csv_path.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="只能导入 .csv 文件")
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="文件不存在，请检查路径")
    if not os.path.isfile(csv_path):
        raise HTTPException(status_code=400, detail="路径不是文件")

    task = _create_task_state(source=csv_path, filename=os.path.basename(csv_path))
    asyncio.create_task(
        _run_import_task(task.task_id, csv_path, csv_path, req.rebuild_trips)
    )

    return {
        "code": 202,
        "data": {
            "task_id": task.task_id,
            "status": task.status,
            "stage": task.stage,
            "progress": task.progress,
            "source": csv_path,
        },
    }


@router.get("/import-tasks/{task_id}")
async def get_import_task(task_id: str):
    task = _get_task_state(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return {"code": 200, "data": task.model_dump()}


@router.get("/import-tasks")
async def list_import_tasks(limit: int = Query(default=6, ge=1, le=20)):
    tasks = _list_task_states(limit)
    return {"code": 200, "data": [task.model_dump() for task in tasks]}
