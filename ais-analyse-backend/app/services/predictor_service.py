from __future__ import annotations

import importlib
import os
import pickle
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.models.analysis import (
    ManualTrackPoint,
    PredictionResponse,
    SimilarTrackItem,
    SimilarTracksResponse,
)


class _STShapeRetriever:
    def __init__(self, index_path: Path):
        if not index_path.exists():
            raise FileNotFoundError(f"Trajectory index not found: {index_path}")
        with open(index_path, "rb") as f:
            self.index = pickle.load(f)

    def query(self, obs: np.ndarray, timestamps: list[int], k: int = 5) -> list[dict[str, Any]]:
        return self.index.query(obs=obs, timestamps=timestamps, K=k)


def _inference_forward(
    btcn: Any,
    attention: Any,
    predictor: Any,
    q_obs: Any,
    r_obs: Any,
    q_scale: Any,
    start_pos: Any,
) -> Any:
    torch = importlib.import_module("torch")
    device = next(btcn.parameters()).device

    q_obs_t = q_obs.permute(0, 2, 1).to(device)
    batch_size, top_k, steps, channels = r_obs.shape
    r_obs_t = r_obs.view(batch_size * top_k, steps, channels).permute(0, 2, 1).to(device)

    h_q = btcn(q_obs_t)
    h_refs = btcn(r_obs_t).view(batch_size, top_k, -1, steps)

    hidden = torch.cat([h_q.unsqueeze(1), h_refs], dim=1)
    h_tilde = attention(hidden)

    pred_disp_norm = predictor(h_tilde, q_obs_t)
    scale = q_scale.unsqueeze(-1).to(device)
    pred_disp_real = pred_disp_norm * scale

    model_mod = importlib.import_module("Mutual_Attention_opt")
    trajectory_metrics = getattr(model_mod, "TrajectoryMetrics")
    pred_pos = trajectory_metrics.displacement_to_position(start_pos.to(device), pred_disp_real)
    return pred_pos


def _normalize_obs(obs_abs: np.ndarray) -> tuple[Any, Any, Any]:
    torch = importlib.import_module("torch")
    obs_abs_tensor = torch.tensor(obs_abs, dtype=torch.float32)
    disp = obs_abs_tensor[1:] - obs_abs_tensor[:-1]
    disp = torch.cat([torch.zeros(1, 2), disp], dim=0)

    scale = disp.abs().max(dim=0).values + 1e-6
    disp_norm = disp / scale
    start_pos = obs_abs_tensor[-1]
    return disp_norm.T, scale, start_pos


@dataclass
class _PredictorRuntime:
    torch: Any
    btcn: Any
    attention: Any
    predictor: Any
    retriever: Any
    device: Any
    t_obs: int
    t_pred: int
    k_default: int


_runtime: _PredictorRuntime | None = None
_OBS_INTERVAL_SECONDS = 30
_PRED_INTERVAL_SECONDS = 30
_MANUAL_PREDICT_POINT_COUNT = 120


def _extract_global_traj_id(sample: dict[str, Any], fallback: int) -> int:
    if "global_traj_id" in sample and sample["global_traj_id"] is not None:
        return int(sample["global_traj_id"])
    return int(fallback)


def _resolve_samples_path(predictor_dir: Path) -> Path | None:
    env_path = os.getenv("PREDICTOR_SAMPLES_PATH", "").strip()
    candidates = [
        Path(env_path) if env_path else None,
        Path(r"D:\MobilityDB\data\samples\global_traj_id.pkl"),
        predictor_dir / "data" / "samples" / "global_traj_id.pkl",
        Path(__file__).resolve().parents[2] / "data" / "samples" / "global_traj_id.pkl",
        Path(__file__).resolve().parents[1] / "data" / "samples" / "global_traj_id.pkl",
    ]
    for p in candidates:
        if p is None:
            continue
        if p.exists():
            return p
    return None


def _ensure_index_file(predictor_dir: Path) -> Path:
    index_path = predictor_dir / "data" / "samples" / "traj_index_v3.pkl"
    if index_path.exists():
        return index_path

    samples_path = _resolve_samples_path(predictor_dir)
    if samples_path is None:
        raise RuntimeError(
            "缺少轨迹样本文件 global_traj_id.pkl，无法构建 traj_index.pkl。"
            "请先执行数据导入的“轨迹处理并生成 pkl”阶段。"
        )

    try:
        st_tree_mod = importlib.import_module("ST_Tree_opt")
        STShapeIndex = getattr(st_tree_mod, "STShapeIndex")
    except Exception as exc:
        raise RuntimeError(f"索引构建模块加载失败：{exc}") from exc

    with open(samples_path, "rb") as f:
        raw_samples = pickle.load(f)

    if not isinstance(raw_samples, list) or len(raw_samples) == 0:
        raise RuntimeError(f"样本文件为空或格式错误：{samples_path}")

    adapted_samples: list[dict[str, Any]] = []
    for i, s in enumerate(raw_samples):
        if not isinstance(s, dict):
            continue
        obs = s.get("obs")
        ts = s.get("timestamps") or s.get("obs_time")
        global_traj_id = _extract_global_traj_id(s, fallback=i)
        if obs is None or ts is None:
            continue
        adapted_samples.append(
            {
                "global_traj_id": global_traj_id,
                "obs": np.asarray(obs, dtype=np.float32),
                "timestamps": np.asarray(ts),
            }
        )

    if not adapted_samples:
        raise RuntimeError(f"样本中缺少 obs/timestamps 字段，无法构建索引：{samples_path}")

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = STShapeIndex(geohash_precision=8, bits_per_shape=8)
    index.build(adapted_samples)

    with open(index_path, "wb") as f:
        pickle.dump(index, f)

    return index_path


def _resample_trajectory(traj: np.ndarray, target_len: int) -> np.ndarray:
    arr = np.asarray(traj, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError("轨迹格式错误，应为 (N,2)")
    if arr.shape[0] < 2:
        raise ValueError("Trajectory must contain at least 2 points")

    deltas = np.linalg.norm(arr[1:] - arr[:-1], axis=1)
    dist = np.concatenate([[0.0], np.cumsum(deltas)])
    total_dist = dist[-1]

    if total_dist < 1e-6:
        return np.repeat(arr[:1], target_len, axis=0)

    target_dist = np.linspace(0.0, float(total_dist), target_len)
    resampled = np.zeros((target_len, 2), dtype=np.float32)

    j = 0
    for i in range(target_len):
        while j < len(dist) - 2 and dist[j + 1] < target_dist[i]:
            j += 1
        ratio = (target_dist[i] - dist[j]) / (dist[j + 1] - dist[j] + 1e-6)
        resampled[i] = arr[j] + ratio * (arr[j + 1] - arr[j])

    return resampled


def _ensure_predictor_import_path() -> Path:
    predictor_dir = Path(__file__).resolve().parent / "predictor"
    predictor_dir_str = str(predictor_dir)
    if predictor_dir_str not in sys.path:
        sys.path.insert(0, predictor_dir_str)
    return predictor_dir


def _load_runtime() -> _PredictorRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime

    predictor_dir = _ensure_predictor_import_path()

    try:
        torch = importlib.import_module("torch")
        model_mod = importlib.import_module("Mutual_Attention_opt")
    except Exception as exc:
        raise RuntimeError(f"predictor 依赖加载失败：{exc}") from exc

    BTCN = getattr(model_mod, "BTCN")
    MutualAttention = getattr(model_mod, "MutualAttention")
    TrajectoryPredictionModule = getattr(model_mod, "TrajectoryPredictionModule")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    t_obs = 120
    t_pred = 360
    k_default = 5
    hidden_dim = 2

    btcn = BTCN(channels=2, hidden_dim=hidden_dim).to(device).eval()
    attention = MutualAttention(
        K_plus_1=k_default + 1,
        T_obs=t_obs,
        hidden_dim=hidden_dim,
    ).to(device).eval()
    predictor = TrajectoryPredictionModule(
        K=k_default,
        T_obs=t_obs,
        T_pred=t_pred,
        d=2,
        hidden_dim=hidden_dim,
    ).to(device).eval()

    ckpt_path = predictor_dir / "model_ep49.pth"
    if not ckpt_path.exists():
        raise RuntimeError(f"模型文件不存在：{ckpt_path}")

    ckpt = torch.load(ckpt_path, map_location=device)

    if not isinstance(ckpt, dict):
        raise RuntimeError("模型 checkpoint 格式错误")

    if "btcn" in ckpt and "attention" in ckpt and "predictor" in ckpt:
        btcn.load_state_dict(ckpt["btcn"], strict=False)
        attention.load_state_dict(ckpt["attention"], strict=False)
        predictor.load_state_dict(ckpt["predictor"], strict=False)
    elif "state_dict" in ckpt and isinstance(ckpt["state_dict"], dict):
        state_dict = ckpt["state_dict"]
        btcn_sd = {k[len("btcn."):]: v for k, v in state_dict.items() if k.startswith("btcn.")}
        attention_sd = {k[len("attention."):]: v for k, v in state_dict.items() if k.startswith("attention.")}
        predictor_sd = {k[len("predictor."):]: v for k, v in state_dict.items() if k.startswith("predictor.")}
        if not btcn_sd or not attention_sd or not predictor_sd:
            raise RuntimeError("state_dict 中缺少 btcn/attention/predictor 权重")
        btcn.load_state_dict(btcn_sd, strict=False)
        attention.load_state_dict(attention_sd, strict=False)
        predictor.load_state_dict(predictor_sd, strict=False)
    else:
        raise RuntimeError("checkpoint 中缺少可识别的 btcn/attention/predictor 权重")

    index_path = _ensure_index_file(predictor_dir)
    retriever = _STShapeRetriever(index_path=index_path)

    _runtime = _PredictorRuntime(
        torch=torch,
        btcn=btcn,
        attention=attention,
        predictor=predictor,
        retriever=retriever,
        device=device,
        t_obs=t_obs,
        t_pred=t_pred,
        k_default=k_default,
    )
    return _runtime


def predict_from_manual_points(
    points: list[ManualTrackPoint],
    duration_minutes: int = 60,
    step_seconds: int = 60,
) -> PredictionResponse:
    # 固定模型 I/O 约束：输入固定 120 点（30s），输出 360 点（30s）
    _ = duration_minutes
    _ = step_seconds

    runtime = _load_runtime()

    if len(points) < 2:
        raise RuntimeError("至少需要 2 个轨迹点")

    # 统一为 (lat, lon)
    obs_latlon_raw = np.array([[p.lat, p.lon] for p in points], dtype=np.float32)
    obs_latlon = _resample_trajectory(obs_latlon_raw, target_len=_MANUAL_PREDICT_POINT_COUNT)

    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    start_ts = now_ts - (runtime.t_obs - 1) * _OBS_INTERVAL_SECONDS
    timestamps = list(range(start_ts, now_ts + 1, _OBS_INTERVAL_SECONDS))

    # 1) normalize obs
    disp_norm, scale, start_pos = _normalize_obs(obs_latlon)
    q_obs = disp_norm.T.unsqueeze(0).to(runtime.device)   # (1, T, 2)
    q_scale = scale.unsqueeze(0).to(runtime.device)       # (1, 2)
    start_pos = start_pos.unsqueeze(0).to(runtime.device) # (1, 2)

    # 2) Top-K retrieval
    refs = runtime.retriever.query(obs=obs_latlon, timestamps=timestamps, k=max(runtime.k_default * 3, runtime.k_default))

    ref_obs_list: list[np.ndarray] = []
    seen_global_traj_ids: set[int] = set()
    for r in refs:
        if not isinstance(r, dict):
            continue
        global_traj_id = _extract_global_traj_id(r, fallback=-1)
        if global_traj_id in seen_global_traj_ids:
            continue
        seen_global_traj_ids.add(global_traj_id)

        sample = r.get("sample") if isinstance(r, dict) else None
        if not isinstance(sample, dict) or "obs" not in sample:
            continue
        r_traj = np.asarray(sample["obs"], dtype=np.float32)
        r_traj = _resample_trajectory(r_traj, target_len=runtime.t_obs)
        r_disp, _, _ = _normalize_obs(r_traj)
        ref_obs_list.append(r_disp.T.cpu().numpy())  # (T_obs, 2)

        if len(ref_obs_list) >= runtime.k_default:
            break

    if not ref_obs_list:
        # 无检索结果时退化为复制 query 作为 ref
        ref_obs_list.append(disp_norm.T.cpu().numpy())

    while len(ref_obs_list) < runtime.k_default:
        ref_obs_list.append(ref_obs_list[-1])

    r_obs = runtime.torch.tensor(np.stack(ref_obs_list[: runtime.k_default]), dtype=runtime.torch.float32)
    r_obs = r_obs.unsqueeze(0).to(runtime.device)  # (1, K, T_obs, 2)

    # 3) inference
    with runtime.torch.no_grad():
        pred_pos = _inference_forward(
            runtime.btcn,
            runtime.attention,
            runtime.predictor,
            q_obs,
            r_obs,
            q_scale,
            start_pos,
        )

    pred_pos = pred_pos[0].detach().cpu().T.numpy()  # (T_pred, 2) lat/lon

    pred_steps = min(runtime.t_pred, pred_pos.shape[0])
    pred_pos = pred_pos[:pred_steps]

    # 输出格式要求 [lon, lat]
    pred_lonlat = [[float(p[1]), float(p[0])] for p in pred_pos]

    pred_timestamps = [
        datetime.fromtimestamp(now_ts + (i + 1) * _PRED_INTERVAL_SECONDS, tz=timezone.utc).isoformat()
        for i in range(pred_steps)
    ]

    confidence = round(min(1.0, max(0.1, len(refs) / max(runtime.k_default, 1))), 2)

    return PredictionResponse(
        mmsi=0,
        predicted_track={
            "type": "LineString",
            "coordinates": pred_lonlat,
        },
        predicted_timestamps=pred_timestamps,
        confidence=confidence,
        method="mutual_attention_opt",
    )


def find_similar_tracks_from_points(
    points: list[ManualTrackPoint],
    top_k: int = 5,
) -> SimilarTracksResponse:
    runtime = _load_runtime()

    if len(points) < 2:
        raise RuntimeError("至少需要 2 个轨迹点")

    obs_latlon_raw = np.array([[p.lat, p.lon] for p in points], dtype=np.float32)
    obs_latlon = _resample_trajectory(obs_latlon_raw, target_len=runtime.t_obs)

    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    start_ts = now_ts - (runtime.t_obs - 1) * _OBS_INTERVAL_SECONDS
    timestamps = list(range(start_ts, now_ts + 1, _OBS_INTERVAL_SECONDS))

    refs = runtime.retriever.query(obs=obs_latlon, timestamps=timestamps, k=max(top_k * 3, top_k))

    tracks: list[SimilarTrackItem] = []
    seen_global_traj_ids: set[int] = set()
    for idx, ref in enumerate(refs, start=1):
        if not isinstance(ref, dict):
            continue

        global_traj_id = _extract_global_traj_id(ref, fallback=idx)
        if global_traj_id in seen_global_traj_ids:
            continue
        seen_global_traj_ids.add(global_traj_id)

        sample = ref.get("sample")
        if not isinstance(sample, dict) or "obs" not in sample:
            continue

        raw_obs = np.asarray(sample["obs"], dtype=np.float32)
        if raw_obs.ndim != 2 or raw_obs.shape[1] != 2 or raw_obs.shape[0] < 2:
            continue

        lonlat_coords = [[float(p[1]), float(p[0])] for p in raw_obs]
        tracks.append(
            SimilarTrackItem(
                rank=len(tracks) + 1,
                global_traj_id=global_traj_id,
                track={
                    "type": "LineString",
                    "coordinates": lonlat_coords,
                },
            )
        )

        if len(tracks) >= top_k:
            break

    return SimilarTracksResponse(tracks=tracks)
