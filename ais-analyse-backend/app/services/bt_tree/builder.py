"""
从 MobilityDB 构建 BT-Tree
"""
import time
from datetime import datetime
from typing import List, Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from .models import Trajectory, MinBoundingBox, Query
from .tree import BTTree


class BTTreeBuilder:
    """BT-Tree 构建器"""
    
    def __init__(self, 
                 max_leaf_size: int = 50,
                 max_depth: int = 12,
                 use_cfbc: bool = True,
                 cfbc_config: Optional[Dict] = None):
        self.max_leaf_size = max_leaf_size
        self.max_depth = max_depth
        self.use_cfbc = use_cfbc
        self.cfbc_config = cfbc_config or {}
    
    async def build_from_mobilitydb(self, 
                                    session: AsyncSession,
                                    table_name: str = 'vessels',
                                    limit: Optional[int] = None) -> BTTree:
        """
        从 MobilityDB 加载轨迹数据并构建 BT-Tree
        
        Args:
            session: SQLAlchemy AsyncSession
            table_name: 轨迹表名，默认 'vessels'
            limit: 限制加载的轨迹数量，None 表示全部
        
        Returns:
            构建好的 BTTree 实例
        """
        start_time = time.time()
        
        # 1. 从数据库查询所有轨迹的 MBB
        trajectories = await self._fetch_trajectories(session, table_name, limit)
        
        fetch_time = time.time() - start_time
        print(f"[BT-Tree] 从数据库获取 {len(trajectories)} 条轨迹，耗时 {fetch_time:.2f}s")
        
        if not trajectories:
            # 返回空树
            return BTTree(max_leaf_size=self.max_leaf_size, max_depth=self.max_depth)
        
        # 2. 构建 BT-Tree（使用 CFBM 成本函数）
        tree = BTTree(
            max_leaf_size=self.max_leaf_size, 
            max_depth=self.max_depth,
            use_cfbc=self.use_cfbc,
            cfbc_config=self.cfbc_config
        )
        tree.build(trajectories)
        
        total_time = time.time() - start_time
        stats = tree.get_stats()
        print(f"[BT-Tree] 构建完成: {stats['total_nodes']} 节点, "
              f"深度 {stats['max_depth']}, 平均叶大小 {stats['avg_leaf_size']}, "
              f"总耗时 {total_time:.2f}s")
        
        return tree
    
    async def _fetch_trajectories(self,
                                 session: AsyncSession,
                                 table_name: str,
                                 limit: Optional[int] = None) -> List[Trajectory]:
        """从数据库获取轨迹 MBB 数据"""
        
        # 使用 MobilityDB 函数查询轨迹的包围盒
        # 注意：使用 ::geometry 转换来获取边界框
        query = f"""
        SELECT 
            mmsi,
            vessel_name,
            startTimestamp(trip) as tmin,
            endTimestamp(trip) as tmax,
            ST_XMin(trajectory(trip)::geometry) as xmin,
            ST_XMax(trajectory(trip)::geometry) as xmax,
            ST_YMin(trajectory(trip)::geometry) as ymin,
            ST_YMax(trajectory(trip)::geometry) as ymax
        FROM {table_name}
        WHERE trip IS NOT NULL
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        result = await session.execute(text(query))
        rows = result.mappings().all()
        
        trajectories = []
        for row in rows:
            try:
                # 处理可能的 NULL 值
                xmin = row['xmin']
                xmax = row['xmax']
                ymin = row['ymin']
                ymax = row['ymax']
                
                # 跳过无效数据
                if None in (xmin, xmax, ymin, ymax, row['tmin'], row['tmax']):
                    continue
                
                traj = Trajectory(
                    id=row['mmsi'],
                    name=row['vessel_name'],
                    mbb=MinBoundingBox(
                        xmin=float(xmin),
                        xmax=float(xmax),
                        ymin=float(ymin),
                        ymax=float(ymax),
                        tmin=row['tmin'],
                        tmax=row['tmax']
                    )
                )
                trajectories.append(traj)
            except (TypeError, ValueError) as e:
                print(f"[BT-Tree] 跳过无效轨迹 MMSI={row['mmsi']}: {e}")
                continue
        
        return trajectories
    
    async def build_with_query_workload(self,
                                       session: AsyncSession,
                                       table_name: str = 'vessels',
                                       query_workload: Optional[List[Query]] = None,
                                       limit: Optional[int] = None) -> BTTree:
        """
        使用查询负载优化构建 BT-Tree
        
        Args:
            session: SQLAlchemy AsyncSession
            table_name: 轨迹表名
            query_workload: 查询负载（用于优化切分）
            limit: 限制加载的轨迹数量
        """
        # 获取轨迹数据
        trajectories = await self._fetch_trajectories(session, table_name, limit)
        
        if not trajectories:
            return BTTree(
                max_leaf_size=self.max_leaf_size, 
                max_depth=self.max_depth,
                use_cfbc=self.use_cfbc,
                cfbc_config=self.cfbc_config
            )
        
        # 构建树（使用查询负载）
        tree = BTTree(
            max_leaf_size=self.max_leaf_size, 
            max_depth=self.max_depth,
            use_cfbc=self.use_cfbc,
            cfbc_config=self.cfbc_config
        )
        tree.build(trajectories, query_workload)
        
        return tree


async def build_btree_index(session: AsyncSession,
                           max_leaf_size: int = 50,
                           max_depth: int = 12) -> BTTree:
    """
    便捷函数：从 MobilityDB 构建 BT-Tree 索引
    
    使用示例:
        tree = await build_btree_index(session)
    """
    builder = BTTreeBuilder(max_leaf_size=max_leaf_size, max_depth=max_depth)
    return await builder.build_from_mobilitydb(session)
