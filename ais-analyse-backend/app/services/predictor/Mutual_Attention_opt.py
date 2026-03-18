import torch
import torch.nn as nn
import torch.nn.functional as F
import pickle
import numpy as np


# --- 工具函数 ---
Hidden_dim=2
def load_pkl(path):
    with open(path, "rb") as f:
        return pickle.load(f)


def to_relative_displacement(traj):
    """
    Convert absolute trajectory to relative displacement representation.
    traj: (T, 2) -> (T, 2)
    """
    disp = np.zeros_like(traj)
    disp[1:] = traj[1:] - traj[:-1]
    return disp


def normalize_displacement(disp, quantile=0.99, scale_factor=None):
    """
    稳健归一化
    """
    if scale_factor is None:
        abs_disp = np.abs(disp)
        scale_factor = np.percentile(abs_disp, quantile * 100, axis=0)
        scale_factor = np.maximum(scale_factor, 1e-6)
    norm_disp = disp / scale_factor
    norm_disp = np.clip(norm_disp, -1, 1)
    return norm_disp, scale_factor


def historical_trajectory_selection(
        query_id,
        sample_dict,
        topk_results,
        device="cpu"  # 实际上我们在Dataset中调用，建议返回numpy，device参数保留兼容性但暂不使用
):
    """
    数据预处理：返回 Numpy 数组，由 Dataset 转 Tensor
    Returns
    -------
    query_norm : np.ndarray (T_obs, 2)
    ref_norm_list : List[np.ndarray] (K, T_obs, 2)
    q_scale : np.ndarray (2,)
    """

    # ---------- query ----------
    query_sample = sample_dict[query_id]
    q_abs = query_sample["obs"]

    # 处理类型，确保是 numpy
    if isinstance(q_abs, list): q_abs = np.array(q_abs)

    q_disp_raw = to_relative_displacement(q_abs)#查询轨迹相对坐标
    q_disp, q_scale = normalize_displacement(q_disp_raw)#查询轨迹归一化坐标，归一化因子

    # 此时 q_disp 是 (T_obs, 2)

    # ---------- reference ----------
    ref_trajs = []
    ref_ids = topk_results[query_id]

    target_len = q_disp.shape[0]

    for rid in ref_ids:
        if rid not in sample_dict:
            continue

        obs = sample_dict[rid]["obs"]
        if isinstance(obs, list): obs = np.array(obs)

        if len(obs) < 2:
            continue

        r_disp_raw = to_relative_displacement(obs)
        r_disp, _ = normalize_displacement(r_disp_raw)

        # 长度对齐
        if r_disp.shape[0] != target_len:
            if r_disp.shape[0] > target_len:
                r_disp = r_disp[:target_len]
            else:
                pad_len = target_len - r_disp.shape[0]
                pad = np.zeros((pad_len, 2))
                r_disp = np.vstack([r_disp, pad])

        ref_trajs.append(r_disp)

    # 如果没有参考轨迹，复制自身填充 (防止空列表报错)
    if len(ref_trajs) == 0:
        ref_trajs = [q_disp.copy()]

    # 返回纯 Numpy 数据
    return q_disp, ref_trajs, q_scale


# --- 模型模块 ---

class CausalConv1d(nn.Module):
    def __init__(self, channels, kernel_size, dilation, is_forward=True):
        super().__init__()
        self.is_forward = is_forward
        self.pad = (kernel_size - 1) * dilation
        self.conv = nn.Conv1d(channels, channels, kernel_size, padding=0, dilation=dilation)

    def forward(self, x):
        if self.is_forward:
            x = F.pad(x, (self.pad, 0))
        else:
            x = F.pad(x, (0, self.pad))
        return self.conv(x)


class BTCNLayer(nn.Module):
    def __init__(self, channels=2, kernel_size=3, dilation=1):
        super().__init__()
        self.conv_fwd = CausalConv1d(channels, kernel_size, dilation, True)
        self.conv_rev = CausalConv1d(channels, kernel_size, dilation, False)
        self.relu = nn.ReLU()
        self.elu = nn.ELU()
        self.W_out = nn.Conv1d(2 * channels, channels, 1)

    def forward(self, h_fwd, h_rev):
        # Residual connection
        delta_fwd = self.relu(self.conv_fwd(h_fwd))
        h_fwd = h_fwd + delta_fwd

        delta_rev = self.relu(self.conv_rev(h_rev))
        h_rev = h_rev + delta_rev

        # Gate / Projection
        h_cat = torch.cat([self.elu(h_fwd), self.elu(h_rev)], dim=1)
        h = self.W_out(h_cat)
        return h_fwd, h_rev, h


class BTCN(nn.Module):
    def __init__(self, num_layers=5, channels=2, hidden_dim=Hidden_dim, kernel_size=3):
        super().__init__()
        self.input_proj = nn.Conv1d(channels, hidden_dim, kernel_size=1)
        self.layers = nn.ModuleList([
            BTCNLayer(hidden_dim, kernel_size, 2 ** l)
            for l in range(num_layers)
        ])

    def forward(self, x):
        # x input shape: (B, 2, T)
        x = self.input_proj(x)  # -> (B, 64, T)

        h_fwd = x
        h_rev = torch.flip(x, dims=[2])
        skip_connections = []

        for layer in self.layers:
            h_fwd, h_rev, skip = layer(h_fwd, h_rev)
            skip_connections.append(skip)

        return sum(skip_connections)


class MutualAttention(nn.Module):
    def __init__(self, K_plus_1, T_obs, hidden_dim):
        super().__init__()
        self.W1 = nn.ParameterList([nn.Parameter(torch.randn(T_obs, T_obs)) for _ in range(K_plus_1)])
        self.W2 = nn.ParameterList([nn.Parameter(torch.randn(hidden_dim, 1)) for _ in range(K_plus_1)])

        for p in self.parameters():
            if p.dim() > 1: nn.init.xavier_uniform_(p)

    def forward(self, h):
        # h: (B, K+1, d, T)
        B, K_plus_1, d, T = h.shape
        enhanced = []
        for k in range(K_plus_1):
            hk = h[:, k, :, :]  # (B, d, T)
            hk_t = hk.permute(0, 2, 1)  # (B, T, d)

            # Attention Score
            temp = torch.einsum('ij,bjk->bik', self.W1[k], hk_t)
            score = torch.einsum('bjc,cd->bjd', temp, self.W2[k])  # (B, T, 1)
            a_k = torch.softmax(torch.tanh(score), dim=1).permute(0, 2, 1)  # (B, 1, T)

            enhanced.append(hk * a_k)  # Broadcasting (B, d, T)

        return torch.stack(enhanced, dim=1).mean(dim=1)


class TrajectoryPredictionModule(nn.Module):
    def __init__(self, K, T_obs, T_pred, d, hidden_dim):
        super().__init__()
        self.T_pred = T_pred
        self.W3 = nn.Linear(hidden_dim * T_obs, T_obs)
        self.W4 = nn.Linear(d * T_obs, T_obs)
        self.W5 = nn.Linear(2 * T_obs, T_pred * d)
        self.relu = nn.ReLU()

    def forward(self, h_tilde, L_tau):
        B = h_tilde.shape[0]
        h_flat = h_tilde.reshape(B, -1)
        L_flat = L_tau.reshape(B, -1)

        feat = torch.cat([self.relu(self.W3(h_flat)), self.relu(self.W4(L_flat))], dim=1)
        output = self.W5(feat)
        return output.reshape(B, 2, self.T_pred)


# --- 指标计算模块 ---

class TrajectoryMetrics:
    @staticmethod
    def haversine_km(pos1, pos2):
        """
        pos1, pos2: (B, 2, T) or (B, 2)
        纬度在 [:,0], 经度在 [:,1]
        返回: (B, T) 或 (B,)
        """
        R = 6371.0  # Earth radius in km

        lat1 = torch.deg2rad(pos1[:, 0])
        lon1 = torch.deg2rad(pos1[:, 1])
        lat2 = torch.deg2rad(pos2[:, 0])
        lon2 = torch.deg2rad(pos2[:, 1])

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = torch.sin(dlat / 2) ** 2 + \
            torch.cos(lat1) * torch.cos(lat2) * torch.sin(dlon / 2) ** 2

        c = 2 * torch.asin(torch.clamp(a.sqrt(), max=1.0))
        return R * c

    @staticmethod
    def ADE_km(pred_pos, gt_pos):
        """
        pred_pos, gt_pos: (B, 2, T_pred)
        返回: scalar ADE (km)
        """
        # (B, T_pred)
        dist = TrajectoryMetrics.haversine_km(pred_pos, gt_pos)

        # per-trajectory mean
        ade_per_traj = dist.mean(dim=1)

        # dataset / batch mean
        return ade_per_traj.mean()

    @staticmethod
    def displacement_to_position(start_pos, pred_disp):
        """
        修正后的简化版本，专门配合 process_batch 使用
        Args:
            start_pos: (B, 2) 观测序列的最后一个绝对坐标
            pred_disp: (B, 2, T_pred) 真实尺度的位移序列
        Returns:
            pred_pos: (B, 2, T_pred) 绝对坐标
        """
        # 累加位移
        cumsum_disp = torch.cumsum(pred_disp, dim=2)
        # 加上起点 (需要扩展维度以广播)
        # start_pos (B, 2) -> (B, 2, 1)
        pred_pos = cumsum_disp + start_pos.unsqueeze(-1)
        return pred_pos

    @staticmethod
    def ADE(pred_pos, gt_pos):
        # Euclidean distance
        return torch.norm(pred_pos - gt_pos, dim=1).mean()

    @staticmethod
    def FDE(pred_pos, gt_pos):
        # Final point distance
        return torch.norm(pred_pos[:, :, -1] - gt_pos[:, :, -1], dim=1).mean()

    @staticmethod
    def ADE_window(pred_pos, gt_pos, horizon):
        """
        pred_pos, gt_pos: (B, 2, T_pred)
        horizon: int, 时间步数（120 / 240 / 360）
        返回: scalar ADE (km)
        """
        # 截取前 horizon 步
        pred_w = pred_pos[:, :, :horizon]
        gt_w = gt_pos[:, :, :horizon]

        # (B, horizon)
        dist = TrajectoryMetrics.haversine_km(pred_w, gt_w)

        # per trajectory mean → batch mean
        return dist.mean(dim=1).mean()

    @staticmethod
    def GT_total_displacement(gt_pos):
        return TrajectoryMetrics.haversine_km(
            gt_pos[:, :, 0],
            gt_pos[:, :, -1]
        )




