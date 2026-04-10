"""
BT-Tree 主类 - 构建和管理二叉轨迹树
"""
import time
from datetime import datetime
from typing import List, Optional, Dict, Tuple
from .models import BTNode, Trajectory, Query, MinBoundingBox
from .split_policy import CFBMSplitPolicy, MedianSplitPolicy


class BTTree:
    """Binary Trajectory Tree - 二叉轨迹树"""
    
    def __init__(self, 
                 max_leaf_size: int = 50, 
                 max_depth: int = 12,
                 use_cfbc: bool = True,
                 cfbc_config: Optional[Dict] = None):
        """
        初始化 BT-Tree
        
        Args:
            max_leaf_size: 叶节点最大轨迹数，超过则分裂
            max_depth: 最大树深度，防止过深
            use_cfbc: 是否使用 CFBM 成本函数（默认 True），False 则使用简单中位数切分
            cfbc_config: CFBM 配置参数，如 {"alpha": 1.0, "beta": 2.0, "gamma": 1.5}
        """
        self.root: Optional[BTNode] = None
        self.max_leaf_size = max_leaf_size
        self.max_depth = max_depth
        
        # 切分策略
        cfbc_config = cfbc_config or {}
        if use_cfbc:
            # 使用优化的 CFBM（采样加速）
            # 默认 λ=0.25 (实验验证查询性能最优)
            if 'lambda_param' not in cfbc_config:
                cfbc_config['lambda_param'] = 0.25
            self.split_policy = CFBMSplitPolicy(**cfbc_config)
        else:
            self.split_policy = MedianSplitPolicy()
        
        # 轨迹存储
        self.trajectories: Dict[int, Trajectory] = {}
        
        # 节点计数器
        self.next_node_id = 0
        
        # 统计信息
        self.build_time_ms = 0.0
        self.total_nodes = 0
    
    def build(self, 
             trajectories: List[Trajectory], 
             query_workload: Optional[List[Query]] = None) -> None:
        """
        构建 BT-Tree
        
        Args:
            trajectories: 所有轨迹列表
            query_workload: 查询负载（用于优化切分），为 None 时使用默认策略
        """
        start_time = time.time()
        
        # 存储轨迹
        self.trajectories = {t.id: t for t in trajectories}
        
        # 如果没有查询负载，生成一些默认的范围查询作为参考
        if query_workload is None:
            query_workload = self._generate_default_queries(trajectories)
        
        # 创建根节点
        all_ids = [t.id for t in trajectories]
        self.next_node_id = 0
        self.root = self._build_node(all_ids, query_workload, depth=0)
        
        # 统计
        self.build_time_ms = (time.time() - start_time) * 1000
        self.total_nodes = self.next_node_id
    
    def _build_node(self, 
                   traj_ids: List[int], 
                   query_workload: List[Query],
                   depth: int) -> BTNode:
        """递归构建节点 - 使用 CFBM 成本函数选择最优切分"""
        import logging
        logger = logging.getLogger("btree")
        
        # 创建节点（始终存储 trajectories，用于生成候选切分）
        node = BTNode(
            node_id=self.next_node_id,
            mbb=self._compute_mbb(traj_ids),
            trajectories=traj_ids,
            is_leaf=True
        )
        self.next_node_id += 1
        
        # 停止条件检查
        if len(traj_ids) <= self.max_leaf_size or depth >= self.max_depth:
            node.is_leaf = True
            node.trajectories = traj_ids
            return node
        
        # 使用切分策略找到最优切分
        best_split = self.split_policy.select_best_split(
            node, self.trajectories, query_workload
        )
        
        if best_split is None:
            # 无法找到有效切分，作为叶节点
            node.is_leaf = True
            node.trajectories = traj_ids
            return node
        
        # 执行切分
        axis, position = best_split
        left_ids, right_ids = self.split_policy._execute_split(
            traj_ids, self.trajectories, axis, position
        )
        
        # 如果切分后一边为空，停止
        if len(left_ids) == 0 or len(right_ids) == 0:
            node.is_leaf = True
            node.trajectories = traj_ids
            return node
        
        # 设置为内部节点
        node.is_leaf = False
        node.split_axis = axis
        node.split_pos = position
        node.trajectories = []  # 内部节点不存储轨迹
        
        # 递归构建子树
        node.left = self._build_node(left_ids, query_workload, depth + 1)
        node.right = self._build_node(right_ids, query_workload, depth + 1)
        
        return node
    
    def _compute_mbb(self, traj_ids: List[int]) -> MinBoundingBox:
        """计算一组轨迹的包围盒"""
        if not traj_ids:
            now = datetime.now()
            return MinBoundingBox(0, 0, 0, 0, now, now)
        
        mbbs = [self.trajectories[tid].mbb for tid in traj_ids if tid in self.trajectories]
        
        if not mbbs:
            now = datetime.now()
            return MinBoundingBox(0, 0, 0, 0, now, now)
        
        return MinBoundingBox(
            xmin=min(m.xmin for m in mbbs),
            xmax=max(m.xmax for m in mbbs),
            ymin=min(m.ymin for m in mbbs),
            ymax=max(m.ymax for m in mbbs),
            tmin=min(m.tmin for m in mbbs),
            tmax=max(m.tmax for m in mbbs)
        )
    
    def _generate_default_queries(self, trajectories: List[Trajectory]) -> List[Query]:
        """
        生成默认查询负载（用于没有提供查询负载时）
        
        策略：基于数据分布生成一些典型的范围查询
        """
        if not trajectories:
            return []
        
        # 计算全局 MBB
        global_mbb = MinBoundingBox(
            xmin=min(t.mbb.xmin for t in trajectories),
            xmax=max(t.mbb.xmax for t in trajectories),
            ymin=min(t.mbb.ymin for t in trajectories),
            ymax=max(t.mbb.ymax for t in trajectories),
            tmin=min(t.mbb.tmin for t in trajectories),
            tmax=max(t.mbb.tmax for t in trajectories)
        )
        
        queries = []
        
        # 生成几个不同大小的典型查询
        # 25% 小范围查询
        # 50% 中范围查询
        # 25% 大范围查询
        
        # 计算时间和空间跨度
        time_span = (global_mbb.tmax - global_mbb.tmin).total_seconds()
        x_span = global_mbb.xmax - global_mbb.xmin
        y_span = global_mbb.ymax - global_mbb.ymin
        
        # 小范围 (10%)
        for _ in range(2):
            queries.append(self._create_subquery(global_mbb, 0.1, time_span))
        
        # 中范围 (30%)
        for _ in range(4):
            queries.append(self._create_subquery(global_mbb, 0.3, time_span))
        
        # 大范围 (60%)
        for _ in range(2):
            queries.append(self._create_subquery(global_mbb, 0.6, time_span))
        
        return queries
    
    def _create_subquery(self, 
                        global_mbb: MinBoundingBox, 
                        ratio: float,
                        time_span: float) -> Query:
        """创建一个子范围查询"""
        import random
        
        x_span = global_mbb.xmax - global_mbb.xmin
        y_span = global_mbb.ymax - global_mbb.ymin
        
        # 随机起始点
        x_start = global_mbb.xmin + random.random() * x_span * (1 - ratio)
        y_start = global_mbb.ymin + random.random() * y_span * (1 - ratio)
        time_start_offset = random.random() * time_span * (1 - ratio)
        
        return Query(
            mbb=MinBoundingBox(
                xmin=x_start,
                xmax=x_start + x_span * ratio,
                ymin=y_start,
                ymax=y_start + y_span * ratio,
                tmin=datetime.fromtimestamp(
                    global_mbb.tmin.timestamp() + time_start_offset
                ),
                tmax=datetime.fromtimestamp(
                    global_mbb.tmin.timestamp() + time_start_offset + time_span * ratio
                )
            ),
            weight=1.0
        )
    
    def get_stats(self) -> Dict:
        """获取树的统计信息"""
        if self.root is None:
            return {
                'total_nodes': 0,
                'max_depth': 0,
                'avg_leaf_size': 0,
                'total_trajectories': 0,
                'build_time_ms': 0
            }
        
        leaf_sizes = []
        
        def collect_stats(node: BTNode, depth: int) -> Tuple[int, int]:
            """递归收集统计信息，返回 (节点数, 最大深度)"""
            if node is None:
                return 0, depth - 1
            
            if node.is_leaf:
                leaf_sizes.append(len(node.trajectories))
                return 1, depth
            
            left_count, left_depth = collect_stats(node.left, depth + 1)
            right_count, right_depth = collect_stats(node.right, depth + 1)
            
            return 1 + left_count + right_count, max(left_depth, right_depth)
        
        total_nodes, max_depth = collect_stats(self.root, 1)
        
        import statistics
        avg_leaf_size = statistics.mean(leaf_sizes) if leaf_sizes else 0
        
        return {
            'total_nodes': total_nodes,
            'max_depth': max_depth,
            'avg_leaf_size': round(avg_leaf_size, 2),
            'min_leaf_size': min(leaf_sizes) if leaf_sizes else 0,
            'max_leaf_size': max(leaf_sizes) if leaf_sizes else 0,
            'total_trajectories': len(self.trajectories),
            'build_time_ms': round(self.build_time_ms, 2)
        }
    
    def to_dict(self) -> Dict:
        """将树序列化为字典"""
        return {
            'max_leaf_size': self.max_leaf_size,
            'max_depth': self.max_depth,
            'stats': self.get_stats(),
            'root': self.root.to_dict() if self.root else None
        }
