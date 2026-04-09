"""
BT-Tree 查询算法 - 范围查询和 KNN 查询
"""
import time
import math
from datetime import datetime, timezone
from typing import List, Tuple, Optional, Dict, Set
from .models import BTNode, Trajectory, MinBoundingBox
from .tree import BTTree


class BTTreeQuery:
    """BT-Tree 查询处理器"""
    
    def __init__(self, tree: BTTree):
        self.tree = tree
        # 查询统计
        self.last_query_nodes_visited = 0
        self.last_query_time_ms = 0.0
    
    def range_query(self, query_mbb: MinBoundingBox) -> List[int]:
        """
        3D 范围查询：返回与查询范围相交的轨迹ID列表
        
        Args:
            query_mbb: 查询的三维包围盒 (x, y, t)
        
        Returns:
            相交的轨迹ID列表
        """
        if self.tree.root is None:
            return []
        
        start_time = time.time()
        self.last_query_nodes_visited = 0
        
        result = []
        self._range_query_recursive(self.tree.root, query_mbb, result)
        
        self.last_query_time_ms = (time.time() - start_time) * 1000
        
        # 去重（由于重叠，一个轨迹可能在多个叶节点中）
        return list(set(result))
    
    def _range_query_recursive(self, 
                              node: BTNode, 
                              query_mbb: MinBoundingBox, 
                              result: List[int]) -> None:
        """递归范围查询"""
        
        self.last_query_nodes_visited += 1
        
        # 剪枝：如果查询范围与节点MBB不相交，跳过
        if not node.mbb.intersects(query_mbb):
            return
        
        if node.is_leaf:
            # 叶节点：检查每个轨迹
            for tid in node.trajectories:
                if tid in self.tree.trajectories:
                    traj_mbb = self.tree.trajectories[tid].mbb
                    if traj_mbb.intersects(query_mbb):
                        result.append(tid)
        else:
            # 内部节点：暂时禁用方向剪枝，确保正确性
            # 原因：方向剪枝有边界条件 bug，导致数据丢失
            # 后续：使用保守策略重新实现
            self._range_query_recursive(node.left, query_mbb, result)
            self._range_query_recursive(node.right, query_mbb, result)
    
    def knn_query(self, 
                 query_point: Tuple[float, float, datetime], 
                 k: int,
                 initial_radius_degree: float = 0.1) -> List[Tuple[int, float]]:
        """
        K近邻查询：返回最近的k条轨迹及其距离
        
        策略：先用范围查询缩小候选集，再精确计算
        
        Args:
            query_point: (x, y, t) 查询点
            k: 返回最近邻的数量
            initial_radius_degree: 初始搜索半径（经纬度度数）
        
        Returns:
            List of (trajectory_id, distance)，按距离升序排列
        """
        if self.tree.root is None or k <= 0:
            return []
        
        start_time = time.time()
        self.last_query_nodes_visited = 0
        
        x, y, t = query_point
        
        # 1. 范围扩张策略寻找候选
        candidates = self._find_knn_candidates(query_point, k, initial_radius_degree)
        
        if not candidates:
            self.last_query_time_ms = (time.time() - start_time) * 1000
            return []
        
        # 2. 计算精确距离并排序
        distances = []
        for tid in candidates:
            if tid in self.tree.trajectories:
                dist = self._compute_min_distance(query_point, self.tree.trajectories[tid])
                distances.append((tid, dist))
        
        # 按距离排序
        distances.sort(key=lambda x: x[1])
        
        self.last_query_time_ms = (time.time() - start_time) * 1000
        
        return distances[:k]
    
    def _find_knn_candidates(self, 
                            query_point: Tuple[float, float, datetime],
                            k: int,
                            initial_radius: float) -> Set[int]:
        """通过范围扩张寻找 KNN 候选"""
        x, y, t = query_point
        radius = initial_radius
        max_radius = 180.0  # 最大半径（避免无限扩张）
        
        candidates = set()
        
        while len(candidates) < k * 3 and radius <= max_radius:  # 确保有足够候选
            # 构建查询 MBB（空间范围 + 时间窗口）
            # 时间窗口：前后各1小时
            time_window = 3600  # 1小时 = 3600秒
            
            query_mbb = MinBoundingBox(
                xmin=x - radius,
                xmax=x + radius,
                ymin=y - radius,
                ymax=y + radius,
                tmin=datetime.fromtimestamp(max(0, t.timestamp() - time_window), tz=timezone.utc),
                tmax=datetime.fromtimestamp(t.timestamp() + time_window, tz=timezone.utc)
            )
            
            # 范围查询
            candidates = set(self.range_query(query_mbb))
            
            if len(candidates) >= k:
                break
            
            # 扩大搜索范围
            radius *= 2
        
        return candidates
    
    def _compute_min_distance(self, 
                             query_point: Tuple[float, float, datetime],
                             trajectory: Trajectory) -> float:
        """
        计算查询点到轨迹MBB的最小距离
        
        距离 = 空间距离(度) + 时间距离(归一化)
        """
        x, y, t = query_point
        mbb = trajectory.mbb
        
        # 空间距离（使用简单的欧氏距离，单位：度）
        # 注意：这只是一个简化版本，实际应用可能需要更精确的大地距离
        dx = max(mbb.xmin - x, 0, x - mbb.xmax)
        dy = max(mbb.ymin - y, 0, y - mbb.ymax)
        space_dist = math.sqrt(dx * dx + dy * dy)
        
        # 时间距离（转换为"等效度数"进行归一化）
        # 假设：1小时 ≈ 1度（可调节）
        TIME_SCALE = 1.0 / 3600  # 1秒 = 1/3600 度
        
        t_timestamp = t.timestamp()
        tmin_ts = mbb.tmin.timestamp()
        tmax_ts = mbb.tmax.timestamp()
        
        dt = max(tmin_ts - t_timestamp, 0, t_timestamp - tmax_ts)
        time_dist = dt * TIME_SCALE
        
        # 综合距离
        return math.sqrt(space_dist ** 2 + time_dist ** 2)
    
    def exact_match_query(self, trajectory_id: int) -> Optional[Trajectory]:
        """
        精确匹配查询：根据轨迹ID获取轨迹信息
        
        由于我们有内存中的 trajectories 字典，这实际上是 O(1) 查询
        但如果需要验证轨迹是否存在于树中，可以使用此方法
        """
        if trajectory_id in self.tree.trajectories:
            return self.tree.trajectories[trajectory_id]
        return None
    
    def get_query_stats(self) -> Dict:
        """获取最近一次查询的统计信息"""
        return {
            'last_query_nodes_visited': self.last_query_nodes_visited,
            'last_query_time_ms': round(self.last_query_time_ms, 4)
        }
