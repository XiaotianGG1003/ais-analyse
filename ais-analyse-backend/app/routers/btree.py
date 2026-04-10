"""
BT-Tree API 路由 - 提供时空索引查询接口
"""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.bt_tree_service import btree_service

router = APIRouter(prefix="/api/btree", tags=["btree"])


# ============ Pydantic 模型 ============

class RangeQueryRequest(BaseModel):
    """范围查询请求"""
    xmin: float = Field(..., description="最小经度")
    xmax: float = Field(..., description="最大经度")
    ymin: float = Field(..., description="最小纬度")
    ymax: float = Field(..., description="最大纬度")
    start_time: datetime = Field(..., description="起始时间")
    end_time: datetime = Field(..., description="结束时间")


class RangeQueryResponse(BaseModel):
    """范围查询响应"""
    count: int
    mmsi_list: List[int]
    vessels: List[dict]
    query_stats: dict


class KNNQueryRequest(BaseModel):
    """KNN 查询请求"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")
    timestamp: datetime = Field(..., description="时间")
    k: int = Field(default=10, ge=1, le=100, description="返回数量")
    radius: float = Field(default=0.1, gt=0, description="初始搜索半径（度）")


class KNNQueryResult(BaseModel):
    """KNN 查询结果项"""
    mmsi: int
    distance: float
    name: Optional[str]


class KNNQueryResponse(BaseModel):
    """KNN 查询响应"""
    count: int
    results: List[KNNQueryResult]
    query_stats: dict


class CFBMConfig(BaseModel):
    """
    CFBM 成本函数配置 - 基于论文 SIGMOD 2024
    
    成本函数: Cost = λ * data_skew + (1-λ) * segment_increase
    论文推荐:
    - Geolife/Porto 数据集: λ = 0.5
    - T-Drive 数据集: λ = 0.25
    """
    lambda_param: float = Field(default=0.25, ge=0.0, le=1.0, 
                               description="数据偏斜与段增长的平衡参数 λ（默认0.25，查询最优）")
    n_candidates: int = Field(default=5, ge=3, le=10, description="候选切分数（默认5，减少计算量）")
    sample_size: int = Field(default=200, ge=50, le=500, description="成本计算采样数（默认200，加速80%）")
    use_query_skew: bool = Field(default=False, description="是否启用查询偏斜计算（默认关闭，更快）")


class BuildRequest(BaseModel):
    """构建索引请求"""
    max_leaf_size: int = Field(default=50, ge=10, le=500, description="叶节点最大轨迹数")
    max_depth: int = Field(default=12, ge=3, le=20, description="最大树深度")
    limit: Optional[int] = Field(default=None, ge=1, description="限制加载的轨迹数量（测试用）")
    use_cfbc: bool = Field(default=True, description="是否使用 CFBM 成本函数（False 则使用简单中位数切分）")
    cfbc_config: Optional[CFBMConfig] = Field(default=None, description="CFBM 配置参数")


class BuildResponse(BaseModel):
    """构建索引响应"""
    status: str
    stats: dict


class TreeStatsResponse(BaseModel):
    """树统计信息响应"""
    initialized: bool
    total_nodes: int
    max_depth: int
    avg_leaf_size: float
    total_trajectories: int
    build_time_ms: float


# ============ API 接口 ============

@router.post("/build", response_model=BuildResponse)
async def build_btree(
    request: BuildRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    手动触发 BT-Tree 索引构建
    
    此接口会从 MobilityDB 加载轨迹数据并构建索引。
    使用 CFBM 成本函数优化切分策略（默认启用）。
    """
    try:
        cfbc_config = None
        if request.cfbc_config:
            cfbc_config = {
                "lambda_param": request.cfbc_config.lambda_param,
                "n_candidates": request.cfbc_config.n_candidates,
                "sample_size": request.cfbc_config.sample_size,
                "use_query_skew": request.cfbc_config.use_query_skew
            }
        
        stats = await btree_service.initialize(
            db,
            max_leaf_size=request.max_leaf_size,
            max_depth=request.max_depth,
            limit=request.limit,
            use_cfbc=request.use_cfbc,
            cfbc_config=cfbc_config
        )
        return {
            "status": "success",
            "stats": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"构建索引失败: {str(e)}")


@router.get("/stats", response_model=TreeStatsResponse)
async def get_tree_stats():
    """
    获取 BT-Tree 索引统计信息
    """
    return btree_service.get_stats()


@router.get("/debug/tree-structure")
async def debug_tree_structure(
    xmin: float = Query(..., description="查询范围最小经度"),
    xmax: float = Query(..., description="查询范围最大经度"),
    ymin: float = Query(..., description="查询范围最小纬度"),
    ymax: float = Query(..., description="查询范围最大纬度"),
    start_time: datetime = Query(default=datetime(2025, 1, 1, 0, 0, 0)),
    end_time: datetime = Query(default=datetime(2025, 1, 1, 23, 59, 59))
):
    """
    调试接口：追踪查询遍历过程，找出被错误跳过的节点
    """
    if not btree_service.is_ready():
        raise HTTPException(status_code=400, detail="BT-Tree 未构建")
    
    from app.services.bt_tree.models import MinBoundingBox
    
    query_mbb = MinBoundingBox(
        xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax,
        tmin=start_time,
        tmax=end_time
    )
    
    # 1. 完整遍历 - 记录所有包含相交轨迹的叶节点
    full_scan_leaves = {}  # path -> node info
    
    def full_scan(node, path=""):
        if node is None:
            return
        if node.is_leaf:
            intersect_tids = []
            for tid in node.trajectories:
                if tid in btree_service.tree.trajectories:
                    if btree_service.tree.trajectories[tid].mbb.intersects(query_mbb):
                        intersect_tids.append(tid)
            if intersect_tids:
                full_scan_leaves[path] = {
                    "node_id": node.node_id,
                    "tids": intersect_tids,
                    "mbb": node.mbb.to_dict()
                }
        else:
            full_scan(node.left, path + "L")
            full_scan(node.right, path + "R")
    
    full_scan(btree_service.tree.root)
    
    # 2. 模拟 range_query 遍历 - 记录访问路径
    visited_paths = []
    skipped_paths = []  # 记录被跳过的路径
    
    def trace_query(node, path=""):
        if node is None:
            return
        
        # MBB 剪枝检查
        if not node.mbb.intersects(query_mbb):
            skipped_paths.append({"path": path, "reason": "MBB不相交"})
            return
        
        visited_paths.append(path)
        
        if node.is_leaf:
            return
        
        # 切分方向判断
        split_pos = node.split_pos
        if split_pos is None:
            skipped_paths.append({"path": path, "reason": "split_pos为None"})
            return
        
        split_pos = float(split_pos)
        
        if node.split_axis == 'x':
            query_min, query_max = float(query_mbb.xmin), float(query_mbb.xmax)
        elif node.split_axis == 'y':
            query_min, query_max = float(query_mbb.ymin), float(query_mbb.ymax)
        else:
            query_min = float(query_mbb.tmin.timestamp())
            query_max = float(query_mbb.tmax.timestamp())
        
        epsilon = 1e-9
        go_left = query_min <= split_pos + epsilon
        go_right = query_max >= split_pos - epsilon
        
        if not go_left:
            skipped_paths.append({"path": path + "L", "reason": f"query_min({query_min}) > split_pos({split_pos})"})
        else:
            trace_query(node.left, path + "L")
        
        if not go_right:
            skipped_paths.append({"path": path + "R", "reason": f"query_max({query_max}) < split_pos({split_pos})"})
        else:
            trace_query(node.right, path + "R")
    
    trace_query(btree_service.tree.root)
    
    # 3. 找出丢失的叶节点
    missing_leaves = {}
    for path, info in full_scan_leaves.items():
        if path not in visited_paths:
            # 找到是哪个父节点跳过了这个叶节点
            for skipped in skipped_paths:
                if path.startswith(skipped["path"]):
                    missing_leaves[path] = {
                        "tids": info["tids"][:3],
                        "skipped_at": skipped
                    }
                    break
    
    return {
        "query_mbb": {
            "x": [xmin, xmax],
            "y": [ymin, ymax]
        },
        "total_leaves_with_intersect": len(full_scan_leaves),
        "visited_leaves": len([p for p in visited_paths if p in full_scan_leaves]),
        "missing_leaves": len(missing_leaves),
        "missing_samples": list(missing_leaves.items())[:3],
        "skipped_count": len(skipped_paths)
    }


@router.post("/query/range", response_model=RangeQueryResponse)
async def range_query(request: RangeQueryRequest):
    """
    时空范围查询
    
    查询在给定时空范围内的所有船舶轨迹。
    """
    if not btree_service.is_ready():
        raise HTTPException(
            status_code=400, 
            detail="BT-Tree 索引未构建，请先调用 POST /api/btree/build"
        )
    
    try:
        # 执行范围查询
        mmsi_list = await btree_service.query_vessels_in_range(
            xmin=request.xmin,
            xmax=request.xmax,
            ymin=request.ymin,
            ymax=request.ymax,
            tmin=request.start_time,
            tmax=request.end_time
        )
        
        # 获取详细信息
        vessels = await btree_service.get_vessel_details(mmsi_list)
        
        # 获取查询统计
        query_stats = btree_service.get_query_stats()
        
        return {
            "count": len(vessels),
            "mmsi_list": mmsi_list,
            "vessels": vessels,
            "query_stats": query_stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("/query/range")
async def range_query_get(
    xmin: float = Query(..., description="最小经度"),
    xmax: float = Query(..., description="最大经度"),
    ymin: float = Query(..., description="最小纬度"),
    ymax: float = Query(..., description="最大纬度"),
    start_time: datetime = Query(..., description="起始时间 (ISO 8601)"),
    end_time: datetime = Query(..., description="结束时间 (ISO 8601)")
):
    """
    时空范围查询 (GET 版本)
    
    与 POST 版本功能相同，方便浏览器直接访问测试。
    """
    request = RangeQueryRequest(
        xmin=xmin,
        xmax=xmax,
        ymin=ymin,
        ymax=ymax,
        start_time=start_time,
        end_time=end_time
    )
    return await range_query(request)


@router.post("/query/knn", response_model=KNNQueryResponse)
async def knn_query(request: KNNQueryRequest):
    """
    K近邻查询
    
    查找距离给定点（时空坐标）最近的 k 条轨迹。
    """
    if not btree_service.is_ready():
        raise HTTPException(
            status_code=400, 
            detail="BT-Tree 索引未构建，请先调用 POST /api/btree/build"
        )
    
    try:
        results = await btree_service.query_vessels_near_point(
            x=request.longitude,
            y=request.latitude,
            t=request.timestamp,
            k=request.k,
            radius=request.radius
        )
        
        query_stats = btree_service.get_query_stats()
        
        return {
            "count": len(results),
            "results": [KNNQueryResult(**r) for r in results],
            "query_stats": query_stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("/query/knn")
async def knn_query_get(
    longitude: float = Query(..., description="经度"),
    latitude: float = Query(..., description="纬度"),
    timestamp: datetime = Query(..., description="时间 (ISO 8601)"),
    k: int = Query(default=10, ge=1, le=100, description="返回数量"),
    radius: float = Query(default=0.1, gt=0, description="初始搜索半径（度）")
):
    """
    K近邻查询 (GET 版本)
    """
    request = KNNQueryRequest(
        longitude=longitude,
        latitude=latitude,
        timestamp=timestamp,
        k=k,
        radius=radius
    )
    return await knn_query(request)


@router.post("/rebuild", response_model=BuildResponse)
async def rebuild_btree(
    request: BuildRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    重新构建 BT-Tree 索引
    
    与 build 类似，但用于索引已存在时重新构建。
    """
    try:
        cfbc_config = None
        if request.cfbc_config:
            cfbc_config = {
                "lambda_param": request.cfbc_config.lambda_param,
                "n_candidates": request.cfbc_config.n_candidates,
                "sample_size": request.cfbc_config.sample_size,
                "use_query_skew": request.cfbc_config.use_query_skew
            }
        
        stats = await btree_service.rebuild(
            max_leaf_size=request.max_leaf_size,
            max_depth=request.max_depth,
            use_cfbc=request.use_cfbc,
            cfbc_config=cfbc_config
        )
        return {
            "status": "success",
            "stats": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重建索引失败: {str(e)}")
