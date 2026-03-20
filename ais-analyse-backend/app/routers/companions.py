"""
伴随模式检测模块 (Companion/Convoy Pattern Detection)
检测在相近时间内沿相似航线一起航行的船舶
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import Optional, List
import math

from app.database import get_db

router = APIRouter(prefix="/api/companions", tags=["companions"])


@router.get("/detect", response_model=dict)
async def detect_companions(
    start_time: str = Query(..., description="开始时间 (ISO格式)"),
    end_time: str = Query(..., description="结束时间 (ISO格式)"),
    max_distance_nm: float = Query(2.0, description="最大伴随距离（海里）"),
    min_duration_minutes: int = Query(30, description="最小伴随时长（分钟）"),
    max_vessels: int = Query(50, description="最大分析船舶数，建议50-100"),
    db: AsyncSession = Depends(get_db),
):
    """
    检测指定时间窗口内的伴随模式
    
    算法步骤：
    1. 筛选时间窗口内的船舶轨迹
    2. 按空间网格分组减少计算量
    3. 计算候选对的时空相似度
    4. 提取实际伴随时段
    """
    try:
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(status_code=400, detail="时间格式无效")
    
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")
    
    # 距离转换为度（近似）
    max_distance_deg = max_distance_nm / 60.0  # 1度 ≈ 60海里
    
    try:
        # 步骤1: 获取时间窗口内的船舶轨迹
        query = text("""
            SELECT 
                mmsi,
                vessel_name,
                trip,
                startTimestamp(trip) as trip_start,
                endTimestamp(trip) as trip_end,
                numInstants(trip) as point_count
            FROM vessels
            WHERE trip IS NOT NULL
              AND startTimestamp(trip) <= :end_time
              AND endTimestamp(trip) >= :start_time
            ORDER BY numInstants(trip) DESC
            LIMIT :max_vessels
        """)
        
        result = await db.execute(query, {
            "start_time": start_dt,
            "end_time": end_dt,
            "max_vessels": max_vessels
        })
        vessels = result.fetchall()
        
        if len(vessels) < 2:
            return {
                "code": 200,
                "data": {
                    "total_vessels_analyzed": len(vessels),
                    "companion_pairs": [],
                    "companion_groups": []
                }
            }
        
        # 步骤2: 提取轨迹点用于分析
        vessel_data = []
        for v in vessels:
            # 获取轨迹点
            points_query = text("""
                SELECT 
                    ST_X(valueAtTimestamp(trip, ti)::geometry) as lon,
                    ST_Y(valueAtTimestamp(trip, ti)::geometry) as lat,
                    ti as timestamp,
                    0 as speed
                FROM vessels,
                     unnest(timestamps(trip)) as ti
                WHERE mmsi = :mmsi
                  AND ti BETWEEN :start_time AND :end_time
                ORDER BY ti
            """)
            
            points_result = await db.execute(points_query, {
                "mmsi": v.mmsi,
                "start_time": start_dt,
                "end_time": end_dt
            })
            points = points_result.fetchall()
            
            if len(points) >= 3:  # 至少需要3个点
                vessel_data.append({
                    "mmsi": v.mmsi,
                    "vessel_name": v.vessel_name,
                    "points": points
                })
        
        # 步骤3: 检测伴随对
        companion_pairs = []
        
        for i in range(len(vessel_data)):
            for j in range(i + 1, len(vessel_data)):
                v_a = vessel_data[i]
                v_b = vessel_data[j]
                
                # 分析伴随关系
                companion_info = analyze_companion_pair(
                    v_a, v_b, max_distance_nm, min_duration_minutes
                )
                
                if companion_info:
                    companion_pairs.append(companion_info)
        
        # 步骤4: 检测伴随群组（3艘及以上）
        companion_groups = detect_companion_groups(companion_pairs)
        
        return {
            "code": 200,
            "data": {
                "query_params": {
                    "start_time": start_time,
                    "end_time": end_time,
                    "max_distance_nm": max_distance_nm,
                    "min_duration_minutes": min_duration_minutes
                },
                "total_vessels_analyzed": len(vessel_data),
                "total_pairs_detected": len(companion_pairs),
                "companion_pairs": companion_pairs,
                "companion_groups": companion_groups
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"伴随检测失败: {str(e)}")


def analyze_companion_pair(v_a: dict, v_b: dict, max_distance_nm: float, min_duration_minutes: int) -> Optional[dict]:
    """
    分析一对船舶的伴随关系
    返回伴随信息或None
    """
    points_a = v_a["points"]
    points_b = v_b["points"]
    
    # 找时间重叠点
    companion_segments = []
    current_segment = None
    
    for pa in points_a:
        # 找B在相近时间的点
        closest_pb = find_closest_point_in_time(pa, points_b, max_time_diff_seconds=300)  # 5分钟容差
        
        if closest_pb:
            distance_nm = calculate_distance_nm(pa.lat, pa.lon, closest_pb.lat, closest_pb.lon)
            
            if distance_nm <= max_distance_nm:
                if current_segment is None:
                    current_segment = {
                        "start_time": pa.timestamp,
                        "points": []
                    }
                current_segment["points"].append({
                    "timestamp": pa.timestamp.isoformat() if hasattr(pa.timestamp, 'isoformat') else str(pa.timestamp),
                    "vessel_a": {"lon": pa.lon, "lat": pa.lat, "speed": pa.speed},
                    "vessel_b": {"lon": closest_pb.lon, "lat": closest_pb.lat, "speed": closest_pb.speed},
                    "distance_nm": round(distance_nm, 2)
                })
            else:
                # 距离超出，结束当前段
                if current_segment and len(current_segment["points"]) >= 2:
                    current_segment["end_time"] = current_segment["points"][-1]["timestamp"]
                    companion_segments.append(current_segment)
                current_segment = None
    
    # 结束最后一个段
    if current_segment and len(current_segment["points"]) >= 2:
        current_segment["end_time"] = current_segment["points"][-1]["timestamp"]
        companion_segments.append(current_segment)
    
    # 筛选满足最小时长的段
    valid_segments = []
    for seg in companion_segments:
        start = datetime.fromisoformat(seg["start_time"].replace('Z', '+00:00')) if isinstance(seg["start_time"], str) else seg["start_time"]
        end = datetime.fromisoformat(seg["end_time"].replace('Z', '+00:00')) if isinstance(seg["end_time"], str) else seg["end_time"]
        duration_minutes = (end - start).total_seconds() / 60
        
        if duration_minutes >= min_duration_minutes:
            valid_segments.append({
                "start_time": seg["start_time"] if isinstance(seg["start_time"], str) else seg["start_time"].isoformat(),
                "end_time": seg["end_time"] if isinstance(seg["end_time"], str) else seg["end_time"].isoformat(),
                "duration_minutes": round(duration_minutes, 1),
                "point_count": len(seg["points"]),
                "avg_distance_nm": round(sum(p["distance_nm"] for p in seg["points"]) / len(seg["points"]), 2),
                "min_distance_nm": round(min(p["distance_nm"] for p in seg["points"]), 2),
                "max_distance_nm": round(max(p["distance_nm"] for p in seg["points"]), 2),
                "path": seg["points"]
            })
    
    if not valid_segments:
        return None
    
    # 计算总体统计
    total_duration = sum(s["duration_minutes"] for s in valid_segments)
    total_points = sum(s["point_count"] for s in valid_segments)
    
    return {
        "mmsi_a": v_a["mmsi"],
        "vessel_name_a": v_a["vessel_name"],
        "mmsi_b": v_b["mmsi"],
        "vessel_name_b": v_b["vessel_name"],
        "is_companion": True,
        "companion_score": round(min(100, total_duration / 10), 1),  # 伴随评分
        "total_duration_minutes": round(total_duration, 1),
        "total_companion_points": total_points,
        "segment_count": len(valid_segments),
        "segments": valid_segments
    }


def find_closest_point_in_time(target_point, candidate_points, max_time_diff_seconds: int):
    """找时间上最接近的点"""
    target_time = target_point.timestamp
    if isinstance(target_time, str):
        target_time = datetime.fromisoformat(target_time.replace('Z', '+00:00'))
    
    closest = None
    min_diff = max_time_diff_seconds
    
    for cp in candidate_points:
        cp_time = cp.timestamp
        if isinstance(cp_time, str):
            cp_time = datetime.fromisoformat(cp_time.replace('Z', '+00:00'))
        
        diff = abs((cp_time - target_time).total_seconds())
        if diff < min_diff:
            min_diff = diff
            closest = cp
    
    return closest


def calculate_distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """计算两点间距离（海里）"""
    R = 3440.065  # 地球半径（海里）
    
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


def detect_companion_groups(pairs: List[dict]) -> List[dict]:
    """
    从伴随对中检测伴随群组（3艘及以上船舶一起航行）
    使用并查集或简单连通分量检测
    """
    if not pairs:
        return []
    
    # 构建邻接表
    adjacency = {}
    for p in pairs:
        mmsi_a = p["mmsi_a"]
        mmsi_b = p["mmsi_b"]
        
        if mmsi_a not in adjacency:
            adjacency[mmsi_a] = {"name": p["vessel_name_a"], "connections": set()}
        if mmsi_b not in adjacency:
            adjacency[mmsi_b] = {"name": p["vessel_name_b"], "connections": set()}
        
        adjacency[mmsi_a]["connections"].add(mmsi_b)
        adjacency[mmsi_b]["connections"].add(mmsi_a)
    
    # 找连通分量（群组）
    visited = set()
    groups = []
    
    def dfs(node, group):
        if node in visited:
            return
        visited.add(node)
        group.append({"mmsi": node, "vessel_name": adjacency[node]["name"]})
        for neighbor in adjacency[node]["connections"]:
            dfs(neighbor, group)
    
    for node in adjacency:
        if node not in visited:
            group = []
            dfs(node, group)
            if len(group) >= 3:
                groups.append({
                    "group_size": len(group),
                    "vessels": group,
                    "total_companion_pairs": sum(1 for p in pairs 
                                                if p["mmsi_a"] in [v["mmsi"] for v in group]
                                                and p["mmsi_b"] in [v["mmsi"] for v in group])
                })
    
    return groups


@router.get("/{mmsi}/companions", response_model=dict)
async def get_vessel_companions(
    mmsi: int,
    start_time: str = Query(..., description="开始时间 (ISO格式)"),
    end_time: str = Query(..., description="结束时间 (ISO格式)"),
    max_distance_nm: float = Query(2.0, description="最大伴随距离（海里）"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取指定船舶的伴随关系
    """
    if not (100000000 <= mmsi <= 999999999):
        raise HTTPException(status_code=400, detail="MMSI 格式无效")
    
    # 复用 detect API 的逻辑，但只返回指定船舶的伴随
    result = await detect_companions(
        start_time=start_time,
        end_time=end_time,
        max_distance_nm=max_distance_nm,
        db=db
    )
    
    data = result["data"]
    
    # 筛选与指定船舶相关的伴随
    related_pairs = [
        p for p in data["companion_pairs"]
        if p["mmsi_a"] == mmsi or p["mmsi_b"] == mmsi
    ]
    
    return {
        "code": 200,
        "data": {
            "mmsi": mmsi,
            "total_companions": len(related_pairs),
            "companion_pairs": related_pairs
        }
    }
