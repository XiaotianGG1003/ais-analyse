"""
优化的 CFBM 切分策略 - 采样 + 简化
"""
import random
from datetime import datetime, timezone
from typing import List, Tuple, Optional, Dict
from .models import BTNode, Trajectory, Query, MinBoundingBox


class OptimizedCFBMSplitPolicy:
    """
    优化的 CFBM 策略：
    1. 采样减少计算量
    2. 简化成本函数（可选去掉查询负载）
    3. 减少候选切分数
    """
    
    def __init__(self, 
                 alpha: float = 1.0,
                 beta: float = 2.0,
                 gamma: float = 1.5,
                 n_candidates: int = 5,      # 从 10 降到 5
                 sample_size: int = 200,      # 最多采样 200 条轨迹计算成本
                 use_query_skew: bool = True  # 是否使用查询负载优化
                 ):
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.n_candidates = n_candidates
        self.sample_size = sample_size
        self.use_query_skew = use_query_skew
    
    def select_best_split(self, 
                         node: BTNode, 
                         trajectories: Dict[int, Trajectory],
                         query_workload: List[Query]) -> Optional[Tuple[str, float]]:
        """优化的切分选择"""
        if len(node.trajectories) <= 1:
            return None
        
        # 采样轨迹用于成本计算
        sampled_traj_ids = self._sample_trajectories(node.trajectories)
        
        candidates = self._generate_candidates(node, trajectories)
        if not candidates:
            return None
        
        best_split = None
        best_cost = float('inf')
        
        for axis, position in candidates:
            # 在采样上快速评估
            left_sampled, right_sampled = self._execute_split(
                sampled_traj_ids, trajectories, axis, position
            )
            
            if len(left_sampled) == 0 or len(right_sampled) == 0:
                continue
            
            # 简化的成本计算（只在采样上计算）
            cost = self._compute_cost_fast(
                left_sampled, right_sampled, sampled_traj_ids,
                trajectories, query_workload
            )
            
            if cost < best_cost:
                best_cost = cost
                best_split = (axis, position)
        
        return best_split
    
    def _sample_trajectories(self, traj_ids: List[int]) -> List[int]:
        """采样轨迹用于成本计算"""
        if len(traj_ids) <= self.sample_size:
            return traj_ids
        return random.sample(traj_ids, self.sample_size)
    
    def _generate_candidates(self, 
                            node: BTNode, 
                            trajectories: Dict[int, Trajectory]) -> List[Tuple[str, float]]:
        """生成候选切分"""
        candidates = []
        x_vals, y_vals, t_vals = [], [], []
        
        for traj_id in node.trajectories:
            if traj_id not in trajectories:
                continue
            mbb = trajectories[traj_id].mbb
            x_vals.append((mbb.xmin + mbb.xmax) / 2)
            y_vals.append((mbb.ymin + mbb.ymax) / 2)
            t_vals.append((mbb.tmin.timestamp() + mbb.tmax.timestamp()) / 2)
        
        for axis, vals in [('x', x_vals), ('y', y_vals), ('t', t_vals)]:
            if len(vals) < 3:
                continue
            sorted_vals = sorted(set(vals))
            n = len(sorted_vals)
            
            # 减少候选数
            step = max(1, n // (self.n_candidates + 2))
            for i in range(1, self.n_candidates + 1):
                idx = min(i * step, n - 2)
                if 0 < idx < n - 1:
                    val = sorted_vals[idx]
                    if axis == 't':
                        val = datetime.fromtimestamp(val, tz=timezone.utc)
                    candidates.append((axis, val))
        
        return candidates
    
    def _execute_split(self, 
                      traj_ids: List[int], 
                      trajectories: Dict[int, Trajectory],
                      axis: str, 
                      position: float) -> Tuple[List[int], List[int]]:
        """执行切分"""
        left, right = [], []
        
        for tid in traj_ids:
            if tid not in trajectories:
                continue
            mbb = trajectories[tid].mbb
            
            if axis == 'x':
                val_center = (mbb.xmin + mbb.xmax) / 2
            elif axis == 'y':
                val_center = (mbb.ymin + mbb.ymax) / 2
            else:
                val_center = (mbb.tmin.timestamp() + mbb.tmax.timestamp()) / 2
                if isinstance(position, datetime):
                    position = position.timestamp()
            
            if val_center <= position:
                left.append(tid)
            else:
                right.append(tid)
        
        return left, right
    
    def _compute_cost_fast(self, 
                          left_ids: List[int], 
                          right_ids: List[int],
                          total_sampled: List[int],
                          trajectories: Dict[int, Trajectory],
                          query_workload: List[Query]) -> float:
        """快速成本计算（在采样上）"""
        cost = 0.0
        left_count, right_count = len(left_ids), len(right_ids)
        total_count = len(total_sampled)
        
        if total_count > 0:
            # 数据偏斜
            imbalance = abs(left_count - right_count) / total_count
            cost += self.alpha * imbalance
            
            # 重叠（只在采样上计算）
            overlap = len(set(left_ids) & set(right_ids))
            cost += self.gamma * (overlap / total_count)
        
        # 可选：查询偏斜（如果启用且查询负载不大）
        if self.use_query_skew and query_workload and len(query_workload) < 100:
            query_skew = self._compute_query_skew_sampled(
                left_ids, right_ids, trajectories, query_workload
            )
            cost += self.beta * query_skew
        
        return cost
    
    def _compute_query_skew_sampled(self, left_ids, right_ids, trajectories, query_workload):
        """在采样上计算查询偏斜"""
        left_set, right_set = set(left_ids), set(right_ids)
        left_weight, right_weight = 0.0, 0.0
        
        for query in query_workload:
            left_overlap = any(
                trajectories[tid].mbb.intersects(query.mbb) 
                for tid in left_set if tid in trajectories
            )
            right_overlap = any(
                trajectories[tid].mbb.intersects(query.mbb) 
                for tid in right_set if tid in trajectories
            )
            
            if left_overlap:
                left_weight += query.weight
            if right_overlap:
                right_weight += query.weight
        
        total_weight = left_weight + right_weight
        return abs(left_weight - right_weight) / total_weight if total_weight > 0 else 0


class FastMedianSplitPolicy:
    """最快的纯中位数切分 - 无成本计算"""
    
    def select_best_split(self, 
                         node: BTNode, 
                         trajectories: Dict[int, Trajectory],
                         query_workload: List[Query]) -> Optional[Tuple[str, float]]:
        """极简中位数切分"""
        if len(node.trajectories) <= 1:
            return None
        
        # 随机采样找跨度最大的维度
        sample_size = min(100, len(node.trajectories))
        sampled = random.sample(node.trajectories, sample_size)
        
        x_vals, y_vals, t_vals = [], [], []
        for tid in sampled:
            if tid not in trajectories:
                continue
            mbb = trajectories[tid].mbb
            x_vals.append((mbb.xmin + mbb.xmax) / 2)
            y_vals.append((mbb.ymin + mbb.ymax) / 2)
            t_vals.append((mbb.tmin.timestamp() + mbb.tmax.timestamp()) / 2)
        
        if not x_vals or not y_vals or not t_vals:
            return None
        
        # 选跨度最大的
        spans = [
            ('x', max(x_vals) - min(x_vals), x_vals),
            ('y', max(y_vals) - min(y_vals), y_vals),
            ('t', max(t_vals) - min(t_vals), t_vals)
        ]
        spans.sort(key=lambda x: x[1], reverse=True)
        
        axis, span, vals = spans[0]
        if span <= 0:
            return None
        
        # 用采样的中位数
        median_val = sorted(vals)[len(vals) // 2]
        if axis == 't':
            median_val = datetime.fromtimestamp(median_val, tz=timezone.utc)
        
        return (axis, median_val)
