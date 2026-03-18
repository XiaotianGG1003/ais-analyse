"""
AIS 轨迹数据处理流水线：CSV → MobilityDB → 预测样本 pkl

用法（直接运行）
----------------
# 导入单个 CSV 并生成样本
python data_process/data_process.py --input D:/MobilityDB/ais-2025-01-01.csv

# 导入目录下所有 CSV
python data_process/data_process.py --input mmsi_split/

# 只生成指定 MMSI 的样本
python data_process/data_process.py --input data/ --mmsi 123456789 987654321

# 自定义连接和输出路径
python data_process/data_process.py --input data/ \\
    --host localhost --port 25432 \\
    --dbname mobilitydb --user postgres --password 19930323 \\
    --table ais_segments \\
    --out data/samples/global_traj_id.pkl

输出格式
--------
保存为 pickle 文件，内容为 list[dict]，每个 sample 包含：
    obs        : np.ndarray (120, 2)  float32  [lat, lon] 观测序列
    pred       : np.ndarray (360, 2)  float32  [lat, lon] 真实未来轨迹
    segment_id : int                           轨迹段编号
    mmsi       : int                           船舶标识
    start_time : pd.Timestamp                  obs 起始时间
    obs_time   : np.ndarray (120,)  int64      obs 各点 Unix 时间戳(秒)
    global_traj_id : int                       全局样本编号
"""

import argparse
import os
import glob
import pickle
import hashlib
import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from scipy.interpolate import CubicSpline

# =========================
# 全局参数
# =========================

INTERVAL_SEC = 30

OBS_LEN = 120
PRED_LEN = 360
WINDOW_LEN = OBS_LEN + PRED_LEN
STRIDE = 120

MAX_SPEED_KMH = 185.0          # 100 knots
MAX_TIME_GAP_SEC = 30 * 60     # 30 min
MIN_DURATION_SEC = 4 * 3600    # 4 h


# =========================
# 工具函数
# =========================

def has_abnormal_speed(seg_df, max_speed_kmh=MAX_SPEED_KMH):
    if len(seg_df) < 2:
        return False

    lat = np.radians(seg_df["Latitude"].values)
    lon = np.radians(seg_df["Longitude"].values)

    dlat = np.diff(lat)
    dlon = np.diff(lon)

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2) ** 2
    )
    c = 2 * np.arcsin(np.sqrt(a))
    dist_km = 6371.0 * c

    dt = np.diff(
        seg_df["Timestamp"].values
    ).astype("timedelta64[s]").astype(float)

    dt_hours = dt / 3600.0
    valid = dt_hours > 0

    if not np.any(valid):
        return False

    speed = dist_km[valid] / dt_hours[valid]
    return np.any(speed > max_speed_kmh)

def has_abnormal_speed_interp(df_interp, max_speed_kmh=MAX_SPEED_KMH):
    if len(df_interp) < 2:
        return False

    lat = np.radians(df_interp["Latitude"].values)
    lon = np.radians(df_interp["Longitude"].values)

    dlat = np.diff(lat)
    dlon = np.diff(lon)

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2) ** 2
    )
    c = 2 * np.arcsin(np.sqrt(a))
    dist_km = 6371.0 * c

    # 插值后 dt 恒定
    dt_hours = INTERVAL_SEC / 3600.0

    speed = dist_km / dt_hours
    return np.any(speed > max_speed_kmh)

def spline_interpolate_segment(seg_df, interval_sec=INTERVAL_SEC):
    if len(seg_df) < 4:
        return None

    t = (
        seg_df["Timestamp"].values
        .astype("datetime64[s]")
        .astype(np.int64)
    )

    t0, t1 = t[0], t[-1]
    t_new = np.arange(t0, t1 + interval_sec, interval_sec)

    if len(t_new) < WINDOW_LEN:
        return None

    lat = seg_df["Latitude"].values
    lon = seg_df["Longitude"].values

    try:
        cs_lat = CubicSpline(t, lat)
        cs_lon = CubicSpline(t, lon)
    except Exception:
        return None

    lat_new = cs_lat(t_new)
    lon_new = cs_lon(t_new)

    return pd.DataFrame(
        {
            "Timestamp": pd.to_datetime(t_new, unit="s"),
            "Latitude": lat_new,
            "Longitude": lon_new,
        }
    )


def sliding_window_samples(seg_df_interp, segment_id, mmsi):
    samples = []

    xy = seg_df_interp[["Latitude", "Longitude"]].values
    times = (
        seg_df_interp["Timestamp"]
        .values.astype("datetime64[s]")
        .astype(np.int64)
    )

    N = len(xy)

    for start in range(0, N - WINDOW_LEN + 1, STRIDE):
        obs = xy[start : start + OBS_LEN]
        pred = xy[start + OBS_LEN : start + WINDOW_LEN]

        sample = {
            "obs": obs.astype(np.float32),
            "pred": pred.astype(np.float32),
            "segment_id": segment_id,
            "mmsi": int(mmsi),
            "start_time": pd.to_datetime(times[start], unit="s"),
            "obs_time": times[start : start + OBS_LEN],
        }
        samples.append(sample)

    return samples


# =========================
# CSV → MobilityDB 导入
# =========================

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id            SERIAL PRIMARY KEY,
    mmsi          INTEGER,
    trip          tgeompoint,
    trip_hash     TEXT
);
CREATE INDEX IF NOT EXISTS {table}_mmsi_idx ON {table}(mmsi);
"""

# 额外列定义：列名 → SQL 类型（用于 ALTER TABLE 补列）
_EXTRA_COL_DEFS = {
    "sog":         "REAL",
    "cog":         "REAL",
    "heading":     "REAL",
    "vessel_name": "TEXT",
    "imo":         "TEXT",
    "call_sign":   "TEXT",
    "vessel_type": "TEXT",
    "status":      "TEXT",
    "length":      "REAL",
    "width":       "REAL",
    "draft":       "REAL",
    "cargo":       "TEXT",
    "transceiver": "TEXT",
}


def ensure_mobilitydb_table(cur, table=None):
    """建表并补齐所有额外列（完全幂等）。

    无论表是全新创建还是已存在，均通过 ALTER TABLE ADD COLUMN IF NOT EXISTS
    确保所有额外列都存在，不会重复添加也不会报错。
    """
    if table is None:
        table = MOBILITYDB_TABLE

    # 建表（表已存在则跳过）
    for stmt in _CREATE_TABLE_SQL.format(table=table).strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)

    # 补充额外列（列已存在则跳过）
    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS trip_hash TEXT")

    # 首次迁移时才做全表重扫描（补哈希+去重+建唯一索引）
    idx_name = f"{table}_uniq_mmsi_triphash"
    cur.execute(f"SELECT to_regclass('public.{idx_name}')")
    uniq_idx = cur.fetchone()[0]
    if uniq_idx is None:
        cur.execute(f"UPDATE {table} SET trip_hash = md5(trip::text) WHERE trip_hash IS NULL")
        cur.execute(
            f"""
            DELETE FROM {table} a
            USING {table} b
            WHERE a.ctid < b.ctid
              AND a.mmsi = b.mmsi
              AND a.trip_hash = b.trip_hash
            """
        )
        cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name} ON {table}(mmsi, trip_hash)")

    for col, col_type in _EXTRA_COL_DEFS.items():
        cur.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
        )


def _segment_to_tgeompoint_literal(seg_df):
    """将一段 DataFrame 转成 MobilityDB tgeompoint 字面量字符串。

    格式：{POINT(lon lat)@2023-01-01 00:00:00+00, ...}
    """
    parts = []
    for row in seg_df.itertuples(index=False):
        # 统一转为 UTC，格式化为 MobilityDB 接受的时间字符串
        ts = pd.Timestamp(row.Timestamp).tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S+00")
        parts.append(f"POINT({row.Longitude:.6f} {row.Latitude:.6f})@{ts}")
    return "{" + ",".join(parts) + "}"


# 额外列：CSV 原始字段（统一小写后的列名 → DB 列名）
_EXTRA_COLS = {
    "sog":         "sog",
    "cog":         "cog",
    "heading":     "heading",
    "vessel_name": "vessel_name",
    "imo":         "imo",
    "call_sign":   "call_sign",
    "vessel_type": "vessel_type",
    "status":      "status",
    "length":      "length",
    "width":       "width",
    "draft":       "draft",
    "cargo":       "cargo",
    "transceiver": "transceiver",
}


def _insert_mmsi_df_to_db(cur, df, mmsi, table):
    """将单个 MMSI 的 DataFrame 按时间缺口切段后逐段插入 MobilityDB。

    额外列（sog/cog/vessel_name 等）取每段第一行的值随段记录存储。
    """
    df = df.sort_values("Timestamp").reset_index(drop=True)
    # 去除重复时间戳（保留第一条），MobilityDB 要求时间戳严格递增
    df = df.drop_duplicates(subset=["Timestamp"], keep="first").reset_index(drop=True)
    seg_start = 0
    inserted = 0

    # 确定本批数据实际存在的额外列
    present_extra = {src: dst for src, dst in _EXTRA_COLS.items() if src in df.columns}

    rows_to_insert = []

    def _collect_insert_row(seg):
        if len(seg) < 2:
            return
        literal = _segment_to_tgeompoint_literal(seg)
        trip_hash = hashlib.md5(literal.encode("utf-8")).hexdigest()
        if present_extra:
            first = seg.iloc[0]
            extra_vals = [
                None if pd.isna(first[src])
                else first[src].item() if hasattr(first[src], "item")
                else first[src]
                for src in present_extra
            ]
            rows_to_insert.append((int(mmsi), literal, trip_hash, *extra_vals))
        else:
            rows_to_insert.append((int(mmsi), literal, trip_hash))

    for i in range(1, len(df)):
        dt = (df["Timestamp"].iloc[i] - df["Timestamp"].iloc[i - 1]).total_seconds()
        if dt > MAX_TIME_GAP_SEC:
            _collect_insert_row(df.iloc[seg_start:i])
            seg_start = i

    _collect_insert_row(df.iloc[seg_start:])

    if not rows_to_insert:
        return inserted

    if present_extra:
        cols = "mmsi, trip, trip_hash, " + ", ".join(present_extra.values())
        template = "(" + ", ".join(["%s", "%s::tgeompoint", "%s"] + ["%s"] * len(present_extra)) + ")"
    else:
        cols = "mmsi, trip, trip_hash"
        template = "(%s, %s::tgeompoint, %s)"

    execute_values(
        cur,
        f"INSERT INTO {table}({cols}) VALUES %s ON CONFLICT (mmsi, trip_hash) DO NOTHING",
        rows_to_insert,
        template=template,
        page_size=1000,
    )
    if cur.rowcount and cur.rowcount > 0:
        inserted += cur.rowcount

    return inserted


def import_csv_to_mobilitydb(
    csv_paths,
    conn_params=None,
    table=None,
    truncate=False,
    progress_callback=None,
):
    """将一个或多个 CSV 文件导入 MobilityDB。

    Parameters
    ----------
    csv_paths : str | list[str]
        单个 CSV 文件路径、目录路径，或路径列表。
    conn_params : dict, optional
        数据库连接参数，默认使用 MOBILITYDB_CONN。
    table : str, optional
        目标表名，默认使用 MOBILITYDB_TABLE。
    truncate : bool, optional
        导入前先清空表中所有数据（默认 False）。
        在重新导入同一批 CSV 时置 True 可避免重复数据。

    Returns
    -------
    int  :  成功插入的轨迹段数量
    """
    if conn_params is None:
        conn_params = MOBILITYDB_CONN
    if table is None:
        table = MOBILITYDB_TABLE

    # 统一为列表
    if isinstance(csv_paths, str):
        if os.path.isdir(csv_paths):
            csv_paths = sorted(glob.glob(os.path.join(csv_paths, "*.csv")))
        else:
            csv_paths = [csv_paths]

    if not csv_paths:
        raise ValueError("未找到任何 CSV 文件")

    conn = psycopg2.connect(**conn_params)
    conn.autocommit = False
    cur = conn.cursor()

    ensure_mobilitydb_table(cur, table)
    if truncate:
        cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY")
        print(f"[INFO] 已清空表 {table}")
    conn.commit()

    total_segments = 0
    COMMIT_EVERY = 5000         # 减少提交频率，降低事务提交开销
    CHUNK_SIZE   = 100_000      # 每次读取行数，避免一次性加载整个 CSV

    FIELD_MAP = {
        "mmsi":           "MMSI",
        "base_date_time": "Timestamp",
        "latitude":       "Latitude",
        "longitude":      "Longitude",
    }

    pending = 0

    for idx, csv_path in enumerate(csv_paths, 1):
        try:
            # ---- 阶段 1：分块读取 CSV，按 MMSI 累积数据 ----
            mmsi_buffers = {}   # mmsi -> list[DataFrame]
            total_rows   = 0
            raw_rows_read = 0
            header_ok    = False

            reader = pd.read_csv(csv_path, chunksize=CHUNK_SIZE, low_memory=False)
            for chunk_idx, raw_chunk in enumerate(reader):
                raw_rows_read += len(raw_chunk)

                # 统一列名为小写
                raw_chunk.columns = [c.strip().lower() for c in raw_chunk.columns]

                # 首块检查必需列
                if not header_ok:
                    missing_src = set(FIELD_MAP.keys()) - set(raw_chunk.columns)
                    if missing_src:
                        print(f"  [SKIP] {os.path.basename(csv_path)} 缺少列: {missing_src}")
                        break
                    header_ok = True

                raw_chunk = raw_chunk.rename(columns=FIELD_MAP)

                # 使用显式格式加速解析（比 utc=True+自动推断 快 10-50 倍）
                raw_chunk["Timestamp"] = pd.to_datetime(
                    raw_chunk["Timestamp"],
                    format="%Y-%m-%d %H:%M:%S",
                    errors="coerce",
                    utc=True,
                )
                raw_chunk["Latitude"]  = pd.to_numeric(raw_chunk["Latitude"],  errors="coerce")
                raw_chunk["Longitude"] = pd.to_numeric(raw_chunk["Longitude"], errors="coerce")

                raw_chunk = raw_chunk.dropna(subset=["Latitude", "Longitude", "Timestamp"])
                raw_chunk = raw_chunk[
                    raw_chunk["Latitude"].between(-90, 90) &
                    raw_chunk["Longitude"].between(-180, 180)
                ]

                for mmsi, grp in raw_chunk.groupby("MMSI"):
                    mmsi_buffers.setdefault(mmsi, []).append(grp)

                total_rows += len(raw_chunk)
                if progress_callback:
                    progress_callback(
                        {
                            "phase": "segments",
                            "file_index": idx,
                            "file_count": len(csv_paths),
                            "file_path": csv_path,
                            "rows_read": raw_rows_read,
                            "valid_rows": total_rows,
                            "chunk_index": chunk_idx + 1,
                        }
                    )
                if (chunk_idx + 1) % 10 == 0:
                    print(f"  [INFO] 已读取 {total_rows:,} 行，{len(mmsi_buffers)} 个 MMSI ...", flush=True)

            if not header_ok:
                continue    # SKIP 已在内层打印

            print(f"  [INFO] {os.path.basename(csv_path)} 读取完成：{total_rows:,} 行，{len(mmsi_buffers)} 个 MMSI", flush=True)
            if progress_callback:
                progress_callback(
                    {
                        "phase": "segments",
                        "subphase": "inserting",
                        "file_index": idx,
                        "file_count": len(csv_paths),
                        "file_path": csv_path,
                        "rows_read": raw_rows_read,
                        "valid_rows": total_rows,
                        "processed_mmsi": 0,
                        "total_mmsi": len(mmsi_buffers),
                    }
                )

            # ---- 阶段 2：逐 MMSI 插入数据库 ----
            for mmsi_idx, (mmsi, chunks) in enumerate(mmsi_buffers.items()):
                df_mmsi = pd.concat(chunks, ignore_index=True)
                n = _insert_mmsi_df_to_db(cur, df_mmsi, mmsi, table)
                total_segments += n
                pending        += n

                if pending >= COMMIT_EVERY:
                    conn.commit()
                    pending = 0

                if (mmsi_idx + 1) % 2000 == 0:
                    print(f"  [INFO] 已处理 {mmsi_idx+1}/{len(mmsi_buffers)} 个 MMSI，"
                          f"{total_segments} 个轨迹段", flush=True)

                if progress_callback and ((mmsi_idx + 1) % 200 == 0 or (mmsi_idx + 1) == len(mmsi_buffers)):
                    progress_callback(
                        {
                            "phase": "segments",
                            "subphase": "inserting",
                            "file_index": idx,
                            "file_count": len(csv_paths),
                            "file_path": csv_path,
                            "rows_read": raw_rows_read,
                            "valid_rows": total_rows,
                            "processed_mmsi": mmsi_idx + 1,
                            "total_mmsi": len(mmsi_buffers),
                            "segments_inserted": total_segments,
                        }
                    )

            print(f"  [{idx}/{len(csv_paths)}] {os.path.basename(csv_path)} 导入完成", flush=True)
            if progress_callback:
                progress_callback(
                    {
                        "phase": "segments",
                        "file_index": idx,
                        "file_count": len(csv_paths),
                        "file_path": csv_path,
                        "rows_read": raw_rows_read,
                        "valid_rows": total_rows,
                        "completed": True,
                    }
                )

        except Exception as e:
            conn.rollback()
            pending = 0
            print(f"  [{idx}/{len(csv_paths)}] {os.path.basename(csv_path)} [ERROR] {e}", flush=True)
            continue

    conn.commit()
    cur.close()
    conn.close()

    print(f"[INFO] CSV 导入完成，共插入 {total_segments} 个轨迹段")
    return total_segments


def _process_one_segment(seg_df, segment_id, mmsi):
    samples = []

    if len(seg_df) < 2:
        return samples

    duration = (
        seg_df["Timestamp"].iloc[-1]
        - seg_df["Timestamp"].iloc[0]
    ).total_seconds()

    if duration < MIN_DURATION_SEC:
        return samples

    if has_abnormal_speed(seg_df):
        return samples

    seg_df_interp = spline_interpolate_segment(seg_df)

    if seg_df_interp is None:
        return samples

    if has_abnormal_speed_interp(seg_df_interp):
        return samples

    samples.extend(
        sliding_window_samples(
            seg_df_interp, segment_id, mmsi
        )
    )

    return samples


# =========================
# MobilityDB 数据源
# =========================

# 默认连接参数（与 predictor_api.py 保持一致，可按实际修改）
MOBILITYDB_CONN = {
    "dbname": "mobilitydb",
    "user": "postgres",
    "password": "19930323",
    "host": "localhost",
    "port": 25432,
}

# 存储导入分段轨迹的表（独立于 MobilityDB 内置的 ais_trajectories 表）
# id SERIAL PRIMARY KEY 允许同一 MMSI 存入多个时间段
MOBILITYDB_TABLE = "ais_segments"


def fetch_from_mobilitydb(conn_params=None, table=None, mmsi_list=None):
    """从 MobilityDB 读取轨迹，返回 {mmsi: DataFrame} 字典。

    DataFrame 列：MMSI, Latitude, Longitude, Timestamp

    SQL 原理：
        用 unnest(instants(traj)) 将 tgeompoint 拆为逐点 tinstant，
        再用 getValue / getTimestamp 提取坐标和时间。
    """
    if conn_params is None:
        conn_params = MOBILITYDB_CONN
    if table is None:
        table = MOBILITYDB_TABLE

    # 可选：只拉取指定 MMSI 列表
    where_clause = ""
    params = []
    if mmsi_list:
        placeholders = ",".join(["%s"] * len(mmsi_list))
        where_clause = f"WHERE mmsi IN ({placeholders})"
        params = list(mmsi_list)

    sql = f"""
        SELECT
            t.mmsi,
            ST_Y(getValue(inst)::geometry)  AS lat,
            ST_X(getValue(inst)::geometry)  AS lon,
            getTimestamp(inst)              AS ts
        FROM (
            SELECT mmsi, unnest(instants(trip)) AS inst
            FROM {table}
            {where_clause}
        ) t
        ORDER BY t.mmsi, ts
    """

    conn = psycopg2.connect(**conn_params)
    try:
        cur = conn.cursor()
        cur.execute(sql, params if params else None)
        rows = cur.fetchall()
        col_names = [desc[0] for desc in cur.description]
        cur.close()
    finally:
        conn.close()

    df_all = pd.DataFrame(rows, columns=col_names)
    df_all.columns = ["MMSI", "Latitude", "Longitude", "Timestamp"]
    df_all["Timestamp"] = pd.to_datetime(df_all["Timestamp"])

    return {
        mmsi: grp.reset_index(drop=True)
        for mmsi, grp in df_all.groupby("MMSI")
    }


def _process_mmsi_df(df, mmsi):
    """对单个 MMSI 的 DataFrame 执行与 process_one_mmsi_file 相同的
    切段 → 速度过滤 → 插值 → 滑窗 流程。"""
    df = df.sort_values("Timestamp").drop_duplicates(subset=["Timestamp"]).reset_index(drop=True)

    all_samples = []
    segment_id = 0
    start_idx = 0

    for i in range(1, len(df)):
        dt = (
            df["Timestamp"].iloc[i] - df["Timestamp"].iloc[i - 1]
        ).total_seconds()

        if dt > MAX_TIME_GAP_SEC:
            seg_df = df.iloc[start_idx:i]
            start_idx = i
            segment_id += 1
            all_samples.extend(_process_one_segment(seg_df, segment_id, mmsi))

    # 最后一段
    if start_idx < len(df) - 1:
        seg_df = df.iloc[start_idx:]
        segment_id += 1
        all_samples.extend(_process_one_segment(seg_df, segment_id, mmsi))

    return all_samples


def process_from_mobilitydb(conn_params=None, table=MOBILITYDB_TABLE, mmsi_list=None):
    """从 MobilityDB 读取轨迹并处理为预测模块所需的 sample 列表。

    返回值与 process_one_mmsi_file 相同格式的 list，每个元素为 dict：
        {
            "obs":        np.ndarray (OBS_LEN, 2)   float32  [lat, lon]
            "pred":       np.ndarray (PRED_LEN, 2)  float32  [lat, lon]
            "segment_id": int
            "mmsi":       int
            "start_time": pd.Timestamp
            "obs_time":   np.ndarray (OBS_LEN,)     int64 (unix seconds)
        }
    """
    mmsi_data = fetch_from_mobilitydb(conn_params, table, mmsi_list)

    all_samples = []
    for mmsi, df in mmsi_data.items():
        samples = _process_mmsi_df(df, mmsi)
        all_samples.extend(samples)

    # 写入全局唯一 global_traj_id（与列表下标一致，供 /predict_by_sample 接口使用）
    for idx, sample in enumerate(all_samples):
        sample["global_traj_id"] = idx

    return all_samples


def fetch_from_ais_raw(conn_params=None, table="ais_raw", mmsi_list=None):
    """从 ais_raw 读取原始点位，返回 {mmsi: DataFrame} 字典。"""
    if conn_params is None:
        conn_params = MOBILITYDB_CONN

    where_clause = ""
    params = []
    if mmsi_list:
        placeholders = ",".join(["%s"] * len(mmsi_list))
        where_clause = f"WHERE mmsi IN ({placeholders})"
        params = list(mmsi_list)

    sql = f"""
        SELECT
            mmsi,
            latitude  AS lat,
            longitude AS lon,
            base_date_time AS ts
        FROM {table}
        {where_clause}
        ORDER BY mmsi, ts
    """

    conn = psycopg2.connect(**conn_params)
    try:
        cur = conn.cursor()
        cur.execute(sql, params if params else None)
        rows = cur.fetchall()
        col_names = [desc[0] for desc in cur.description]
        cur.close()
    finally:
        conn.close()

    if not rows:
        return {}

    df_all = pd.DataFrame(rows, columns=col_names)
    df_all.columns = ["MMSI", "Latitude", "Longitude", "Timestamp"]

    return {
        mmsi: grp.reset_index(drop=True)
        for mmsi, grp in df_all.groupby("MMSI")
    }


def process_from_ais_raw(conn_params=None, table="ais_raw", mmsi_list=None, progress_callback=None):
    """从 ais_raw 读取原始数据，清洗/分段后生成样本列表。"""
    mmsi_data = fetch_from_ais_raw(conn_params=conn_params, table=table, mmsi_list=mmsi_list)
    total_vessels = len(mmsi_data)

    all_samples = []
    for idx, (mmsi, df) in enumerate(mmsi_data.items(), 1):
        # 清洗放在处理阶段进行
        df = df.copy()
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce", utc=True)
        df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
        df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")
        df = df.dropna(subset=["Timestamp", "Latitude", "Longitude"])
        df = df[
            df["Latitude"].between(-90, 90) &
            df["Longitude"].between(-180, 180)
        ]
        if df.empty:
            if progress_callback:
                progress_callback(
                    {
                        "processed_vessels": idx,
                        "total_vessels": total_vessels,
                        "mmsi": int(mmsi),
                        "sample_count": len(all_samples),
                    }
                )
            continue

        samples = _process_mmsi_df(df, mmsi)
        all_samples.extend(samples)

        if progress_callback:
            progress_callback(
                {
                    "processed_vessels": idx,
                    "total_vessels": total_vessels,
                    "mmsi": int(mmsi),
                    "sample_count": len(all_samples),
                }
            )

    for sample_idx, sample in enumerate(all_samples):
        sample["global_traj_id"] = sample_idx

    return all_samples


def generate_samples_pkl_from_mobilitydb(
    out_pkl,
    conn_params=None,
    table=MOBILITYDB_TABLE,
    mmsi_list=None,
    progress_callback=None,
):
    """从 MobilityDB 读取轨迹并生成 pkl，支持进度回调。"""
    if conn_params is None:
        conn_params = MOBILITYDB_CONN

    mmsi_data = fetch_from_mobilitydb(conn_params, table, mmsi_list)
    total_vessels = len(mmsi_data)

    all_samples = []
    for idx, (mmsi, df) in enumerate(mmsi_data.items(), 1):
        samples = _process_mmsi_df(df, mmsi)
        all_samples.extend(samples)
        if progress_callback:
            progress_callback(
                {
                    "processed_vessels": idx,
                    "total_vessels": total_vessels,
                    "mmsi": int(mmsi),
                    "sample_count": len(all_samples),
                }
            )

    for sample_idx, sample in enumerate(all_samples):
        sample["global_traj_id"] = sample_idx

    out_pkl = os.path.abspath(out_pkl)
    os.makedirs(os.path.dirname(out_pkl), exist_ok=True)
    with open(out_pkl, "wb") as f:
        pickle.dump(all_samples, f, protocol=pickle.HIGHEST_PROTOCOL)

    if progress_callback:
        progress_callback(
            {
                "processed_vessels": total_vessels,
                "total_vessels": total_vessels,
                "sample_count": len(all_samples),
                "completed": True,
                "out_pkl": out_pkl,
            }
        )

    return {
        "sample_count": len(all_samples),
        "vessel_count": total_vessels,
        "out_pkl": out_pkl,
    }


def generate_samples_pkl_from_ais_raw(
    out_pkl,
    conn_params=None,
    table="ais_raw",
    mmsi_list=None,
    progress_callback=None,
):
    """从 ais_raw 生成 pkl；清洗、分段、插值、滑窗均在此阶段完成。"""
    if conn_params is None:
        conn_params = MOBILITYDB_CONN

    samples = process_from_ais_raw(
        conn_params=conn_params,
        table=table,
        mmsi_list=mmsi_list,
        progress_callback=progress_callback,
    )

    out_pkl = os.path.abspath(out_pkl)
    os.makedirs(os.path.dirname(out_pkl), exist_ok=True)
    with open(out_pkl, "wb") as f:
        pickle.dump(samples, f, protocol=pickle.HIGHEST_PROTOCOL)

    if progress_callback:
        progress_callback(
            {
                "processed_vessels": len({s["mmsi"] for s in samples}) if samples else 0,
                "total_vessels": len({s["mmsi"] for s in samples}) if samples else 0,
                "sample_count": len(samples),
                "completed": True,
                "out_pkl": out_pkl,
            }
        )

    return {
        "sample_count": len(samples),
        "vessel_count": len({s["mmsi"] for s in samples}) if samples else 0,
        "out_pkl": out_pkl,
    }


# =========================
# 完整流水线：CSV → DB → 样本
# =========================

def run_pipeline(csv_paths, out_pkl, conn_params=None, table=None, mmsi_list=None):
    """一步完成：CSV 导入 MobilityDB → 读取处理 → 保存 pkl。

    Parameters
    ----------
    csv_paths : str | list[str]
        CSV 文件路径、目录，或路径列表。
    out_pkl : str
        输出 pkl 文件路径（供 predictor_api.py 和 retrieval 使用）。
    conn_params : dict, optional
        数据库连接参数，默认 MOBILITYDB_CONN。
    table : str, optional
        MobilityDB 表名，默认 MOBILITYDB_TABLE。
    mmsi_list : list[int], optional
        只处理指定 MMSI；None 表示处理全部。
    """
    if conn_params is None:
        conn_params = MOBILITYDB_CONN
    if table is None:
        table = MOBILITYDB_TABLE

    # ---- 步骤 1：CSV → MobilityDB ----
    print("[STEP 1] 将 CSV 数据导入 MobilityDB...")
    import_csv_to_mobilitydb(csv_paths, conn_params, table, truncate=True)

    # ---- 步骤 2：MobilityDB → 样本列表 ----
    print("[STEP 2] 从 MobilityDB 读取并生成样本...")
    samples = process_from_mobilitydb(conn_params, table, mmsi_list)
    print(f"[INFO] 共生成 {len(samples)} 个样本")

    # ---- 步骤 3：保存 pkl ----
    out_pkl = os.path.abspath(out_pkl)
    os.makedirs(os.path.dirname(out_pkl), exist_ok=True)
    with open(out_pkl, "wb") as f:
        pickle.dump(samples, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[INFO] 样本已保存至 {out_pkl}")

    return samples


# =========================
# CLI 入口
# =========================

def _parse_args():
    parser = argparse.ArgumentParser(
        description="AIS 数据处理：CSV → MobilityDB → 轨迹预测样本 pkl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("--input", nargs="+", required=True,
                        help="CSV 文件路径、目录路径或多个文件（空格分隔）")
    parser.add_argument("--host",     default=MOBILITYDB_CONN["host"])
    parser.add_argument("--port",     default=MOBILITYDB_CONN["port"], type=int)
    parser.add_argument("--dbname",   default=MOBILITYDB_CONN["dbname"])
    parser.add_argument("--user",     default=MOBILITYDB_CONN["user"])
    parser.add_argument("--password", default=MOBILITYDB_CONN["password"])
    parser.add_argument("--table",    default=MOBILITYDB_TABLE)
    parser.add_argument("--mmsi", nargs="*", type=int, default=None,
                        help="只生成指定 MMSI 的样本，不指定则处理全部")
    parser.add_argument("--out", default="data/samples/global_traj_id.pkl",
                        help="输出 pkl 文件路径")

    return parser.parse_args()


def main():
    args = _parse_args()

    conn_params = {
        "host":     args.host,
        "port":     args.port,
        "dbname":   args.dbname,
        "user":     args.user,
        "password": args.password,
    }

    csv_paths = args.input if len(args.input) > 1 else args.input[0]

    run_pipeline(
        csv_paths=csv_paths,
        out_pkl=args.out,
        conn_params=conn_params,
        table=args.table,
        mmsi_list=args.mmsi,
    )


if __name__ == "__main__":
    main()
