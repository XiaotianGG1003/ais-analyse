"""
BT-Tree vs MobilityDB 原生查询性能对比
"""
import time
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db
from app.services.bt_tree_service import btree_service

router = APIRouter(prefix="/api/btree-compare", tags=["btree-compare"])


class CompareRequest(BaseModel):
    """对比查询请求"""
    xmin: float = Field(..., description="最小经度")
    xmax: float = Field(..., description="最大经度")
    ymin: float = Field(..., description="最小纬度")
    ymax: float = Field(..., description="最大纬度")
    start_time: datetime = Field(..., description="起始时间")
    end_time: datetime = Field(..., description="结束时间")


class CompareResponse(BaseModel):
    """对比查询响应"""
    query_params: dict
    btree_result: dict
    mobilitydb_result: dict
    comparison: dict


@router.post("/range-query")
async def compare_range_query(
    request: CompareRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    对比 BT-Tree 和 MobilityDB 原生范围查询性能
    
    公平对比：两者都执行时空范围查询，返回范围内的所有船舶
    """
    if not btree_service.is_ready():
        raise HTTPException(
            status_code=400,
            detail="BT-Tree 索引未构建，请先调用 POST /api/btree/build"
        )
    
    results = {
        "query_params": {
            "xmin": request.xmin,
            "xmax": request.xmax,
            "ymin": request.ymin,
            "ymax": request.ymax,
            "start_time": request.start_time.isoformat(),
            "end_time": request.end_time.isoformat()
        },
        "btree_result": {},
        "mobilitydb_result": {},
        "comparison": {}
    }
    
    # ========== BT-Tree 查询 ==========
    bt_start = time.time()
    try:
        # 1. BT-Tree 索引查询（快速过滤）
        bt_mmsi_list = await btree_service.query_vessels_in_range(
            xmin=request.xmin,
            xmax=request.xmax,
            ymin=request.ymin,
            ymax=request.ymax,
            tmin=request.start_time,
            tmax=request.end_time
        )
        bt_index_time = time.time() - bt_start
        
        # 2. 获取详细信息
        bt_vessels = await btree_service.get_vessel_details(bt_mmsi_list)
        bt_total_time = time.time() - bt_start
        
        bt_stats = btree_service.get_query_stats()
        
        results["btree_result"] = {
            "count": len(bt_vessels),
            "index_query_time_ms": round(bt_index_time * 1000, 4),
            "total_time_ms": round(bt_total_time * 1000, 4),
            "nodes_visited": bt_stats.get("last_query_nodes_visited", 0),
            "mmsi_list": bt_mmsi_list[:20] if len(bt_mmsi_list) <= 20 else bt_mmsi_list[:20] + ["..."]
        }
    except Exception as e:
        results["btree_result"] = {"error": str(e)}
    
    # ========== MobilityDB 原生查询 ==========
    md_start = time.time()
    try:
        # 使用 ST_Intersects 替代 stbox && 避免坐标系混合问题
        # 先用简单的空间范围过滤，再精确计算
        
        # 第1步：快速计数（使用边界框比较）
        count_query = text("""
        SELECT COUNT(*) as cnt
        FROM vessels
        WHERE trip IS NOT NULL
          AND ST_XMin(trajectory(trip)::geometry) <= :xmax
          AND ST_XMax(trajectory(trip)::geometry) >= :xmin
          AND ST_YMin(trajectory(trip)::geometry) <= :ymax
          AND ST_YMax(trajectory(trip)::geometry) >= :ymin
          AND startTimestamp(trip) <= :t_end
          AND endTimestamp(trip) >= :t_start
        """)
        
        result = await db.execute(count_query, {
            "xmin": request.xmin, "ymin": request.ymin,
            "xmax": request.xmax, "ymax": request.ymax,
            "t_start": request.start_time, "t_end": request.end_time
        })
        count = result.scalar()
        count_time = time.time() - md_start
        
        # 第2步：获取数据（限制最多100条）
        md_vessels = []
        if count > 0:
            query = text("""
            SELECT mmsi, vessel_name,
                   ST_XMin(trajectory(trip)::geometry) as xmin,
                   ST_XMax(trajectory(trip)::geometry) as xmax,
                   ST_YMin(trajectory(trip)::geometry) as ymin,
                   ST_YMax(trajectory(trip)::geometry) as ymax
            FROM vessels
            WHERE trip IS NOT NULL
              AND ST_XMin(trajectory(trip)::geometry) <= :xmax
              AND ST_XMax(trajectory(trip)::geometry) >= :xmin
              AND ST_YMin(trajectory(trip)::geometry) <= :ymax
              AND ST_YMax(trajectory(trip)::geometry) >= :ymin
              AND startTimestamp(trip) <= :t_end
              AND endTimestamp(trip) >= :t_start
            LIMIT 100
            """)
            result = await db.execute(query, {
                "xmin": request.xmin, "ymin": request.ymin,
                "xmax": request.xmax, "ymax": request.ymax,
                "t_start": request.start_time, "t_end": request.end_time
            })
            rows = result.mappings().all()
            md_vessels = [{"mmsi": row["mmsi"], "vessel_name": row["vessel_name"],
                          "bbox": {"xmin": row["xmin"], "xmax": row["xmax"],
                                  "ymin": row["ymin"], "ymax": row["ymax"]}} for row in rows]
        
        md_total_time = time.time() - md_start
        
        results["mobilitydb_result"] = {
            "count": count,
            "count_time_ms": round(count_time * 1000, 4),
            "total_time_ms": round(md_total_time * 1000, 4),
            "mmsi_list": [v["mmsi"] for v in md_vessels[:20]]
        }
    except Exception as e:
        results["mobilitydb_result"] = {"error": str(e), "time_ms": round((time.time() - md_start) * 1000, 4)}
    
    # ========== 对比分析 ==========
    try:
        bt_count = results["btree_result"].get("count", 0)
        md_count = results["mobilitydb_result"].get("count", 0)
        bt_time = results["btree_result"].get("total_time_ms", 0)
        md_time = results["mobilitydb_result"].get("total_time_ms", 0)
        
        results["comparison"] = {
            "result_match": bt_count == md_count,
            "btree_count": bt_count,
            "mobilitydb_count": md_count,
            "btree_time_ms": bt_time,
            "mobilitydb_time_ms": md_time,
            "speedup": round(md_time / bt_time, 2) if bt_time > 0 else None,
            "winner": "btree" if bt_time < md_time else "mobilitydb" if md_time < bt_time else "tie"
        }
    except Exception as e:
        results["comparison"] = {"error": str(e)}
    
    return results


@router.get("/range-query")
async def compare_range_query_get(
    xmin: float = Query(..., description="最小经度"),
    xmax: float = Query(..., description="最大经度"),
    ymin: float = Query(..., description="最小纬度"),
    ymax: float = Query(..., description="最大纬度"),
    start_time: datetime = Query(..., description="起始时间 (ISO 8601)"),
    end_time: datetime = Query(..., description="结束时间 (ISO 8601)"),
    db: AsyncSession = Depends(get_db)
):
    """对比查询 (GET 版本)"""
    request = CompareRequest(
        xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax,
        start_time=start_time, end_time=end_time
    )
    return await compare_range_query(request, db)
