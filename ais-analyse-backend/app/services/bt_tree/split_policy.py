"""
CFBM (Cost Function Based Method) 启发式切分策略 - 优化版本
"""
import random
from datetime import datetime, timezone
from typing import List, Tuple, Optional, Dict
from .models import BTNode, Trajectory, Query, MinBoundingBox


class CFBMSplitPolicy:
    """
    优化的 CFBM 策略（基于论文 SIGMOD 2024）：
    成本函数: Cost = λ * data_skew + (1-λ) * segment_increase
    
    论文推荐参数:
    - Geolife/Porto 数据集: λ = 0.5
    - T-Drive 数据集: λ = 0.25
    """
    
    def __init__(self, 
                 lambda_param: float = 0.5,    # 论文推荐 0.5 (Geolife/Porto)
                 n_candidates: int = 5,        # 从 10 降到 5
                 sample_size: int = 200,       # 最多采样 200 条轨迹计算成本
                 use_query_skew: bool = False  # 默认关闭查询负载优化（更快）
                 ):
        self.lambda_param = lambda_param  # 替换 alpha/beta/gamma
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
        
        # 采样轨迹用于成本计算（快速筛选）
        sampled_traj_ids = self._sample_trajectories(node.trajectories)
        
        # 生成候选切分（基于全部轨迹，确保覆盖完整范围）
        candidates = self._generate_candidates(node, trajectories)
        if not candidates:
            return None
        
        best_split = None
        best_cost = float('inf')
        
        for axis, position in candidates:
            # 在采样上快速评估切分效果
            left_sampled, right_sampled = self._execute_split(
                sampled_traj_ids, trajectories, axis, position
            )
            
            if len(left_sampled) == 0 or len(right_sampled) == 0:
                continue
            
            # 简化的成本计算（只在采样上计算，快80倍）
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
        # 随机采样，保证代表性
        return random.sample(traj_ids, self.sample_size)
    
    def _generate_candidates(self, 
                            node: BTNode, 
                            trajectories: Dict[int, Trajectory]) -> List[Tuple[str, float]]:
        """生成候选切分（基于全部轨迹的边界）- 统一使用 float"""
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
            
            # 减少候选数（5个而不是10个）
            step = max(1, n // (self.n_candidates + 2))
            for i in range(1, self.n_candidates + 1):
                idx = min(i * step, n - 2)
                if 0 < idx < n - 1:
                    # 统一使用 float，避免 datetime 类型问题
                    candidates.append((axis, float(sorted_vals[idx])))
        
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
        """
        论文 CFBM 成本函数: Cost = λ * data_skew + (1-λ) * segment_increase
        
        - data_skew: 数据偏斜成本 = |left - right| / total
        - segment_increase: 跨越切分线的轨迹比例（产生额外段）
        """
        left_count, right_count = len(left_ids), len(right_ids)
        total_count = len(total_sampled)
        
        if total_count == 0:
            return float('inf')
        
        # 1. 数据偏斜成本 (data_skew)
        data_skew = abs(left_count - right_count) / total_count
        
        # 2. 切分产生的额外轨迹段成本 (segment_increase)
        # 当轨迹跨越切分线时，会被两边都包含
        left_set = set(left_ids)
        right_set = set(right_ids)
        overlapping = len(left_set & right_set)
        segment_increase = overlapping / total_count
        
        # 论文成本函数: λ * data_skew + (1-λ) * segment_increase
        cost = (self.lambda_param * data_skew + 
                (1 - self.lambda_param) * segment_increase)
        
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


class MedianSplitPolicy:
    """最快的纯中位数切分 - 无成本计算，强制平衡"""
    
    def __init__(self):
        pass
    
    def select_best_split(self, 
                         node: BTNode, 
                         trajectories: Dict[int, Trajectory],
                         query_workload: List[Query]) -> Optional[Tuple[str, float]]:
        """极简中位数切分 - 统一使用 float"""
        if len(node.trajectories) <= 1:
            return None
        
        # 采样找跨度最大的维度
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
        
        # 选跨度最大的维度
        spans = [
            ('x', max(x_vals) - min(x_vals), x_vals),
            ('y', max(y_vals) - min(y_vals), y_vals),
            ('t', max(t_vals) - min(t_vals), t_vals)
        ]
        spans.sort(key=lambda x: x[1], reverse=True)
        
        axis, span, vals = spans[0]
        if span <= 0:
            return None
        
        # 用采样的中位数 - 统一使用 float
        median_val = float(sorted(vals)[len(vals) // 2])
        
        return (axis, median_val)


# 别名，保持向后兼容
OptimizedCFBMSplitPolicy = CFBMSplitPolicy
