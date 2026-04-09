"""
BT-Tree 轻量级启发式索引模块

提供用于 AIS 轨迹数据的时空索引加速查询功能。
"""
from .models import BTNode, MinBoundingBox, Trajectory, Query
from .tree import BTTree
from .query import BTTreeQuery
from .split_policy import CFBMSplitPolicy
from .builder import BTTreeBuilder, build_btree_index

__all__ = [
    'BTNode',
    'MinBoundingBox',
    'Trajectory',
    'Query',
    'BTTree',
    'BTTreeQuery',
    'CFBMSplitPolicy',
    'BTTreeBuilder',
    'build_btree_index',
]
