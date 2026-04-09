"""
BT-Tree 核心数据结构定义
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any


def _ensure_utc(dt: datetime) -> datetime:
    """确保 datetime 是带 UTC 时区的"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class MinBoundingBox:
    """最小包围盒 (x, y, t) 三维"""
    xmin: float   # 最小经度
    xmax: float   # 最大经度
    ymin: float   # 最小纬度
    ymax: float   # 最大纬度
    tmin: datetime  # 最早时间
    tmax: datetime  # 最晚时间
    
    def volume(self) -> float:
        """计算3D体积（空间面积 × 时间跨度秒数）"""
        space_area = (self.xmax - self.xmin) * (self.ymax - self.ymin)
        time_span = (self.tmax - self.tmin).total_seconds()
        return space_area * time_span
    
    def intersects(self, other: 'MinBoundingBox') -> bool:
        """判断两个 MBB 是否相交"""
        # 处理时区一致性
        self_tmin = _ensure_utc(self.tmin)
        self_tmax = _ensure_utc(self.tmax)
        other_tmin = _ensure_utc(other.tmin)
        other_tmax = _ensure_utc(other.tmax)
        
        return (
            self.xmin <= other.xmax and self.xmax >= other.xmin and
            self.ymin <= other.ymax and self.ymax >= other.ymin and
            self_tmin <= other_tmax and self_tmax >= other_tmin
        )
    
    def contains(self, other: 'MinBoundingBox') -> bool:
        """判断当前 MBB 是否完全包含另一个 MBB"""
        # 处理时区一致性
        self_tmin = _ensure_utc(self.tmin)
        self_tmax = _ensure_utc(self.tmax)
        other_tmin = _ensure_utc(other.tmin)
        other_tmax = _ensure_utc(other.tmax)
        
        return (
            self.xmin <= other.xmin and self.xmax >= other.xmax and
            self.ymin <= other.ymin and self.ymax >= other.ymax and
            self_tmin <= other_tmin and self_tmax >= other_tmax
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'xmin': self.xmin,
            'xmax': self.xmax,
            'ymin': self.ymin,
            'ymax': self.ymax,
            'tmin': self.tmin.isoformat(),
            'tmax': self.tmax.isoformat()
        }


@dataclass
class Trajectory:
    """轨迹数据"""
    id: int                    # 轨迹ID（这里使用 MMSI）
    name: Optional[str]        # 船名
    mbb: MinBoundingBox        # 最小包围盒
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'mbb': self.mbb.to_dict()
        }


@dataclass
class BTNode:
    """BT-Tree 节点"""
    node_id: int                           # 节点唯一ID
    mbb: MinBoundingBox                    # 节点覆盖的最小包围盒
    is_leaf: bool = True                   # 是否为叶节点
    split_axis: Optional[str] = None       # 切分轴: 'x', 'y', 或 't'
    split_pos: Optional[float] = None      # 切分位置（x/y 为坐标值，t 为时间戳）
    left: Optional['BTNode'] = None        # 左子树
    right: Optional['BTNode'] = None       # 右子树
    trajectories: List[int] = field(default_factory=list)  # 叶节点存储的轨迹ID列表
    
    def __post_init__(self):
        """确保 trajectories 是列表"""
        if self.trajectories is None:
            self.trajectories = []
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            'node_id': self.node_id,
            'mbb': self.mbb.to_dict(),
            'is_leaf': self.is_leaf,
            'split_axis': self.split_axis,
            'split_pos': self.split_pos,
            'trajectories': self.trajectories if self.is_leaf else [],
            'left': self.left.to_dict() if self.left else None,
            'right': self.right.to_dict() if self.right else None
        }


@dataclass
class Query:
    """查询负载（用于优化建树）"""
    mbb: MinBoundingBox
    weight: float = 1.0  # 查询权重（频率）
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'mbb': self.mbb.to_dict(),
            'weight': self.weight
        }
