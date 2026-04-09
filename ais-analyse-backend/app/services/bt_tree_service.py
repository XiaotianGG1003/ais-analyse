"""
BT-Tree 服务层 - 封装 BT-Tree 索引的业务逻辑
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.services.bt_tree import (
    BTTree, 
    BTTreeQuery, 
    BTTreeBuilder,
    MinBoundingBox
)


class BTTreeService:
    """
    BT-Tree 索引服务
    
    提供 BT-Tree 索引的生命周期管理和查询接口
    """
    
    def __init__(self):
        self.tree: Optional[BTTree] = None
        self.query_handler: Optional[BTTreeQuery] = None
        self.db_session: Optional[AsyncSession] = None
        self.is_initialized = False
    
    async def initialize(self, 
                        session: AsyncSession,
                        max_leaf_size: int = 50,
                        max_depth: int = 12,
                        limit: Optional[int] = None,
                        use_cfbc: bool = True,
                        cfbc_config: Optional[Dict] = None) -> Dict[str, Any]:
        """
        从数据库加载并构建 BT-Tree 索引
        
        Args:
            session: SQLAlchemy AsyncSession
            max_leaf_size: 叶节点最大轨迹数
            max_depth: 最大树深度
            limit: 限制加载的轨迹数量
            use_cfbc: 是否使用 CFBM 成本函数（默认 True）
            cfbc_config: CFBM 配置，如 {"alpha": 1.0, "beta": 2.0, "gamma": 1.5}
        
        Returns:
            构建统计信息
        """
        self.db_session = session
        
        builder = BTTreeBuilder(
            max_leaf_size=max_leaf_size, 
            max_depth=max_depth,
            use_cfbc=use_cfbc,
            cfbc_config=cfbc_config
        )
        self.tree = await builder.build_from_mobilitydb(session, limit=limit)
        self.query_handler = BTTreeQuery(self.tree)
        self.is_initialized = True
        
        return self.get_stats()
    
    async def rebuild(self,
                     max_leaf_size: int = 50,
                     max_depth: int = 12,
                     use_cfbc: bool = True,
                     cfbc_config: Optional[Dict] = None) -> Dict[str, Any]:
        """重新构建索引"""
        if self.db_session is None:
            raise RuntimeError("数据库会话未初始化")
        
        return await self.initialize(
            self.db_session, max_leaf_size, max_depth,
            use_cfbc=use_cfbc, cfbc_config=cfbc_config
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计信息"""
        if not self.is_initialized or self.tree is None:
            return {
                'initialized': False,
                'total_nodes': 0,
                'max_depth': 0,
                'avg_leaf_size': 0,
                'total_trajectories': 0,
                'build_time_ms': 0
            }
        
        stats = self.tree.get_stats()
        stats['initialized'] = True
        return stats
    
    async def query_vessels_in_range(self,
                                    xmin: float, 
                                    xmax: float,
                                    ymin: float, 
                                    ymax: float,
                                    tmin: datetime, 
                                    tmax: datetime) -> List[int]:
        """
        时空范围查询 - 返回在范围内的船舶 MMSI 列表
        
        Args:
            xmin, xmax: 经度范围
            ymin, ymax: 纬度范围
            tmin, tmax: 时间范围
        
        Returns:
            MMSI 列表
        """
        if not self.is_initialized or self.query_handler is None:
            raise RuntimeError("BT-Tree 索引未初始化，请先调用 initialize()")
        
        query_mbb = MinBoundingBox(
            xmin=xmin,
            xmax=xmax,
            ymin=ymin,
            ymax=ymax,
            tmin=tmin,
            tmax=tmax
        )
        
        return self.query_handler.range_query(query_mbb)
    
    async def query_vessels_near_point(self,
                                      x: float,
                                      y: float,
                                      t: datetime,
                                      k: int = 10,
                                      radius: float = 0.1) -> List[Dict[str, Any]]:
        """
        K近邻查询 - 查找距离给定点最近的 k 条轨迹
        
        Args:
            x, y: 经纬度
            t: 时间
            k: 返回数量
            radius: 初始搜索半径（度）
        
        Returns:
            包含 MMSI 和距离的字典列表
        """
        if not self.is_initialized or self.query_handler is None:
            raise RuntimeError("BT-Tree 索引未初始化")
        
        results = self.query_handler.knn_query((x, y, t), k, radius)
        
        return [
            {
                'mmsi': tid,
                'distance': round(dist, 6),
                'name': self.tree.trajectories[tid].name if tid in self.tree.trajectories else None
            }
            for tid, dist in results
        ]
    
    async def get_vessel_details(self, mmsi_list: List[int]) -> List[Dict[str, Any]]:
        """
        获取船舶详细信息（从数据库）
        
        Args:
            mmsi_list: MMSI 列表
        
        Returns:
            船舶详细信息列表
        """
        if not mmsi_list:
            return []
        
        if self.db_session is None:
            raise RuntimeError("数据库会话未初始化")
        
        # 使用 SQLAlchemy 执行查询
        query = text("""
        SELECT 
            mmsi, 
            vessel_name,
            startTimestamp(trip) as start_time,
            endTimestamp(trip) as end_time,
            ST_XMin(trajectory(trip)::geometry) as xmin,
            ST_XMax(trajectory(trip)::geometry) as xmax,
            ST_YMin(trajectory(trip)::geometry) as ymin,
            ST_YMax(trajectory(trip)::geometry) as ymax
        FROM vessels
        WHERE mmsi = ANY(:mmsi_list)
        """)
        
        result = await self.db_session.execute(query, {"mmsi_list": mmsi_list})
        rows = result.mappings().all()
        
        return [
            {
                'mmsi': row['mmsi'],
                'vessel_name': row['vessel_name'],
                'start_time': row['start_time'].isoformat() if row['start_time'] else None,
                'end_time': row['end_time'].isoformat() if row['end_time'] else None,
                'bbox': {
                    'xmin': row['xmin'],
                    'xmax': row['xmax'],
                    'ymin': row['ymin'],
                    'ymax': row['ymax']
                }
            }
            for row in rows
        ]
    
    def get_query_stats(self) -> Dict[str, Any]:
        """获取最近一次查询的统计信息"""
        if not self.is_initialized or self.query_handler is None:
            return {'error': 'BT-Tree 未初始化'}
        
        return self.query_handler.get_query_stats()
    
    def is_ready(self) -> bool:
        """检查服务是否已就绪"""
        return self.is_initialized and self.tree is not None


# 全局服务实例
btree_service = BTTreeService()
