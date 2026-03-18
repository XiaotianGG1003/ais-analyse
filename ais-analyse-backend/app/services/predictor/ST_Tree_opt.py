import numpy as np
import pickle
import shapely
import pygeohash
from Shape_encoder_opt import ShapeEncoder

########################################################
# Prefix Binary Tree for ST-Shape
########################################################

class TreeNode:
    """
    Prefix Tree Node
    """

    def __init__(self):
        self.left = None     # bit = 0
        self.right = None    # bit = 1
        self.global_traj_ids = []   # 所有落在该子树下的全局唯一样本 ID


class PrefixBinaryTree:
    """
    Prefix Binary Tree for ST-Shape indexing
    """

    def __init__(self, depth):
        self.root = TreeNode()
        self.depth = depth   # shape code 长度

    ####################################################
    # 插入：离线构建索引
    ####################################################

    def insert(self, shape_code, global_traj_id):
        """
        Insert one trajectory into the prefix tree

        shape_code : List[int]
        global_traj_id : int
        """

        node = self.root
        node.global_traj_ids.append(global_traj_id)

        for bit in shape_code:
            if bit == 0:
                if node.left is None:
                    node.left = TreeNode()
                node = node.left
            else:
                if node.right is None:
                    node.right = TreeNode()
                node = node.right

            # 关键：每一层都保存“子树内所有轨迹”
            node.global_traj_ids.append(global_traj_id)

    ####################################################
    # 查询：Top-K shape 相似轨迹
    ####################################################

    def query(self, shape_code, K, exclude_global_traj_id=None):
        """
        Query Top-K most similar trajectories

        shape_code : List[int]
        K          : int
        exclude_global_traj_id : int or None (避免命中自己)
        """

        results = []
        self._dfs_query(
            node=self.root,
            shape_code=shape_code,
            depth=0,
            K=K,
            results=results,
            exclude_global_traj_id=exclude_global_traj_id
        )
        return results

    ####################################################
    # 核心递归搜索
    ####################################################

    def _dfs_query(self, node, shape_code, depth, K, results, exclude_global_traj_id):
        """
        Depth-first search with prefix pruning
        """

        # 1. 剪枝条件：已经找到足够多
        if node is None or len(results) >= K:
            return

        # 2. 如果该子树样本数 <= 剩余 K，整棵子树加入
        remaining = K - len(results)
        if len(node.global_traj_ids) <= remaining:
            for sid in node.global_traj_ids:
                if exclude_global_traj_id is not None and sid == exclude_global_traj_id:
                    continue
                results.append(sid)
                if len(results) >= K:
                    return
            return

        # 3. 如果已经到达叶子
        if depth == self.depth:
            for sid in node.global_traj_ids:
                if exclude_global_traj_id is not None and sid == exclude_global_traj_id:
                    continue
                results.append(sid)
                if len(results) >= K:
                    return
            return

        # 4. 决定优先走哪个分支（bit 相同）
        bit = shape_code[depth]
        first = node.left if bit == 0 else node.right
        second = node.right if bit == 0 else node.left

        # 5. 优先搜索相同 bit 的分支（XOR = 0）
        self._dfs_query(
            first, shape_code, depth + 1, K, results, exclude_global_traj_id
        )

        # 6. 再搜索相反 bit 的分支（XOR = 1）
        self._dfs_query(
            second, shape_code, depth + 1, K, results, exclude_global_traj_id
        )



class STShapeIndex:
    """
    ST-Shape Data Indexing Module

    Responsibilities:
    - Build ST-Shape codes from sliding windows
    - Construct Prefix Binary Tree
    - Provide Top-K shape similarity query
    """

    def __init__(
        self,
        geohash_precision=8,
        bits_per_shape=8
    ):
        self.encoder = ShapeEncoder(
            geohash_precision=geohash_precision,
            bits_per_shape=bits_per_shape
        )

        self.code_len = 4 * bits_per_shape
        self.tree = PrefixBinaryTree(depth=self.code_len)

        # bookkeeping
        self.sample_db = {}     # global_traj_id -> raw sample
        self.code_db = {}       # global_traj_id -> shape_code

    ####################################################
    # Index construction (offline)
    ####################################################

    def build(self, samples):
        """
        Build ST-Shape index from sliding window samples

        Parameters
        ----------
        samples : List[dict]
            {
                            "global_traj_id": int,
              "obs": np.ndarray (T,2),
              "timestamps": np.ndarray (T,)
            }
        """

        for sample in samples:
            global_traj_id = sample["global_traj_id"]

            shape_code = self.encoder.encode(
                sample["obs"],
                sample["timestamps"]
            )

            self.tree.insert(shape_code, global_traj_id)

            self.sample_db[global_traj_id] = sample
            self.code_db[global_traj_id] = shape_code

    ####################################################
    # Online query
    ####################################################

    def query(self, obs, timestamps, K, exclude_global_traj_id=None):
        """
        Query Top-K shape-similar trajectories

        Parameters
        ----------
        obs : np.ndarray (T,2)
        timestamps : np.ndarray (T,)
        K : int
        exclude_global_traj_id : int or None

        Returns
        -------
        results : List[dict]
            [
              {
                                                                "global_traj_id": int,
                "shape_code": List[int],
                "sample": raw sample
              },
              ...
            ]
        """

        query_code = self.encoder.encode(obs, timestamps)

        global_traj_ids = self.tree.query(
            shape_code=query_code,
            K=K,
            exclude_global_traj_id=exclude_global_traj_id
        )

        results = []
        for sid in global_traj_ids:
            results.append({
                "global_traj_id": sid,
                "shape_code": self.code_db[sid],
                "sample": self.sample_db[sid]
            })

        return results



