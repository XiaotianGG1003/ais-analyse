# AIS 船舶轨迹分析系统 — 后端使用说明

## 目录

- [1. 系统概述](#1-系统概述)
- [2. 技术栈](#2-技术栈)
- [3. 项目结构](#3-项目结构)
- [4. 环境准备](#4-环境准备)
- [5. 快速启动](#5-快速启动)
- [6. 配置说明](#6-配置说明)
- [7. 数据库初始化](#7-数据库初始化)
- [8. API 接口文档](#8-api-接口文档)
- [9. Docker 部署](#9-docker-部署)
- [10. 常见问题](#10-常见问题)

---

## 1. 系统概述

本项目为 AIS 船舶轨迹分析系统的后端服务，基于 FastAPI 构建，使用 PostgreSQL + MobilityDB 作为时空数据库，提供船舶搜索、轨迹查询、航行统计、区域检测、两船距离计算、轨迹预测等核心功能。

## 2. 技术栈

| 组件 | 选型 | 版本要求 |
|------|------|----------|
| 运行时 | Python | ≥ 3.11 |
| Web 框架 | FastAPI | ≥ 0.115.0 |
| ASGI 服务器 | Uvicorn | ≥ 0.30.0 |
| 数据库驱动 | asyncpg | ≥ 0.30.0 |
| ORM | SQLAlchemy (async) | ≥ 2.0.0 |
| 数据验证 | Pydantic v2 | ≥ 2.0.0 |
| 配置管理 | pydantic-settings | ≥ 2.0.0 |
| 数据库 | PostgreSQL + MobilityDB | PG15 + MDB1.1 |
| 科学计算 | NumPy | ≥ 1.26.0 |
| 容器化 | Docker Compose | 3.8 |

## 3. 项目结构

```
ais-analyse-backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 应用入口、CORS、路由注册
│   ├── config.py               # 配置管理（pydantic-settings）
│   ├── database.py             # 异步数据库引擎与会话
│   ├── models/
│   │   ├── vessel.py           # 船舶相关数据模型
│   │   └── analysis.py         # 分析结果数据模型
│   ├── routers/
│   │   ├── vessels.py          # 船舶 CRUD API 路由
│   │   └── analysis.py         # 分析功能 API 路由
│   ├── services/
│   │   ├── vessel_service.py   # 船舶业务逻辑（MobilityDB 查询）
│   │   └── analysis_service.py # 分析业务逻辑（统计 / 检测 / 预测）
│   └── utils/
│       └── geo.py              # 地理计算工具（haversine 等）
├── sql/
│   └── init.sql                # 数据库初始化脚本
├── Dockerfile                  # 容器镜像定义
├── docker-compose.yml          # 容器编排
├── requirements.txt            # Python 依赖
├── pyproject.toml              # 项目元数据
├── .env.example                # 环境变量模板
└── README.md                   # 本文件
```

## 4. 环境准备

### 4.1 本地开发

**前提条件：**
- Python ≥ 3.11
- PostgreSQL 15 + MobilityDB 1.1（推荐使用 Docker 启动）
- pip

```bash
# 1. 克隆项目后进入后端目录
cd ais-analyse-backend

# 2. 创建虚拟环境（推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 复制并编辑环境变量
cp .env.example .env
# 按需修改 .env 中的 DATABASE_URL 和 CORS_ORIGINS
```

### 4.2 数据库（Docker 快速启动）

如果本地没有安装 MobilityDB，可以仅启动数据库容器：

```bash
docker-compose up -d db
```

这将在 `localhost:5432` 启动 PostgreSQL + MobilityDB，默认连接信息：

| 参数 | 值 |
|------|-----|
| 主机 | localhost |
| 端口 | 5432 |
| 数据库 | ais_db |
| 用户名 | ais_user |
| 密码 | ais_password |

## 5. 快速启动

### 5.1 开发模式

```bash
cd ais-analyse-backend

# 启动开发服务器（带热重载）
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

启动成功后：
- API 地址：`http://127.0.0.1:8000`
- 交互式文档（Swagger UI）：`http://127.0.0.1:8000/docs`
- ReDoc 文档：`http://127.0.0.1:8000/redoc`
- 健康检查：`http://127.0.0.1:8000/health`

### 5.2 验证服务

```bash
# 健康检查
curl http://127.0.0.1:8000/health
# 返回: {"status": "ok"}
```

## 6. 配置说明

所有配置项通过环境变量或 `.env` 文件设置，由 `pydantic-settings` 自动加载。

| 环境变量 | 类型 | 默认值 | 说明 |
|----------|------|--------|------|
| `DATABASE_URL` | string | `postgresql+asyncpg://ais_user:ais_password@localhost:5432/ais_db` | 数据库连接字符串 |
| `CORS_ORIGINS` | JSON array | `["http://localhost:5173"]` | 允许跨域的前端地址列表 |
| `MAX_QUERY_DAYS` | int | `30` | 单次查询最大时间跨度（天） |
| `DEFAULT_PAGE_SIZE` | int | `20` | 默认分页大小 |
| `MAX_PAGE_SIZE` | int | `100` | 最大分页大小 |
| `PREDICTION_DEFAULT_MINUTES` | int | `60` | 轨迹预测默认时长（分钟） |
| `PREDICTION_STEP_MINUTES` | int | `5` | 轨迹预测步长（分钟） |

**`.env` 文件示例：**

```env
DATABASE_URL=postgresql+asyncpg://ais_user:ais_password@localhost:5432/ais_db
CORS_ORIGINS=["http://localhost:5173"]
```

## 7. 数据库初始化

### 7.1 自动初始化（Docker）

使用 `docker-compose up db` 时，`sql/init.sql` 会自动执行，完成以下操作：
- 启用 MobilityDB 扩展（自动包含 PostGIS）
- 创建 `ais_raw` 原始数据表及索引
- 创建 `build_vessel_trips()` 存储函数

### 7.2 手动初始化

```bash
psql -h localhost -U ais_user -d ais_db -f sql/init.sql
```

### 7.3 导入 AIS 数据

将 AIS CSV 数据导入 `ais_raw` 表。CSV 字段需与表结构对应：

```bash
# 使用 psql 的 COPY 命令导入
psql -h localhost -U ais_user -d ais_db -c "\COPY ais_raw FROM 'your_ais_data.csv' WITH CSV HEADER"
```

### 7.4 构建轨迹聚合表

数据导入完成后，调用存储函数将原始点位聚合为 MobilityDB 时空轨迹：

```sql
SELECT build_vessel_trips();
```

此操作会：
1. 清除旧的 `vessels` 表（如存在）
2. 对 `ais_raw` 去重后按 MMSI 分组
3. 使用 `tgeogpointSeqSetGaps` 构建时空轨迹（间隙阈值 1 小时）
4. 创建 MMSI 索引

> **注意：** 数据量较大时该操作可能耗时较长，请耐心等待。

### 7.5 数据表结构

#### ais_raw — 原始 AIS 数据

| 字段 | 类型 | 说明 |
|------|------|------|
| mmsi | BIGINT | 水上移动通信业务标识码 |
| base_date_time | TIMESTAMP | 数据时间戳 |
| longitude | DOUBLE PRECISION | 经度 |
| latitude | DOUBLE PRECISION | 纬度 |
| sog | DOUBLE PRECISION | 对地速度（节） |
| cog | DOUBLE PRECISION | 对地航向（度） |
| heading | TEXT | 船首向 |
| vessel_name | TEXT | 船名 |
| imo | TEXT | IMO 编号 |
| call_sign | TEXT | 呼号 |
| vessel_type | INT | 船舶类型编码 |
| status | INT | 航行状态编码 |
| length | DOUBLE PRECISION | 船长（米） |
| width | DOUBLE PRECISION | 船宽（米） |
| draft | DOUBLE PRECISION | 吃水（米） |
| cargo | INT | 货物类型 |
| transceiver | TEXT | 收发器类别 |

#### vessels — 轨迹聚合表

| 字段 | 类型 | 说明 |
|------|------|------|
| mmsi | BIGINT | 水上移动通信业务标识码 |
| vessel_name | TEXT | 船名 |
| trip | tgeogpoint | MobilityDB 时空轨迹对象 |

---

## 8. API 接口文档

所有接口以 JSON 格式返回，统一响应结构为：

```json
{
  "code": 200,
  "data": { ... }
}
```

错误响应使用标准 HTTP 状态码（400 / 404 / 422），格式为：

```json
{
  "detail": "错误描述"
}
```

---

### 8.1 健康检查

```
GET /health
```

**响应示例：**
```json
{ "status": "ok" }
```

---

### 8.2 船舶搜索

```
GET /api/vessels/search?keyword={keyword}&limit={limit}
```

按 MMSI 号码或船名关键字模糊搜索船舶。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| keyword | string | ✅ | — | 搜索关键字（1~100 字符） |
| limit | int | ❌ | 20 | 返回数量（1~100） |

**响应示例：**
```json
{
  "code": 200,
  "data": [
    {
      "mmsi": 366999000,
      "vessel_name": "EVER GIVEN",
      "vessel_type": 70,
      "length": 400.0,
      "width": 59.0
    }
  ]
}
```

---

### 8.3 船舶列表
~~
```
GET /api/vessels?page={page}&page_size={size}&vessel_type={type}
```

分页获取船舶列表，可按船舶类型过滤。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | int | ❌ | 1 | 页码（≥1） |
| page_size | int | ❌ | 20 | 每页数量（1~100，上限受 MAX_PAGE_SIZE 限制） |
| vessel_type | int | ❌ | — | 按船舶类型过滤 |

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "total": 1523,
    "page": 1,
    "page_size": 20,
    "items": [
      {
        "mmsi": 366999000,
        "vessel_name": "EVER GIVEN",
        "vessel_type": 70,
        "length": 400.0,
        "width": 59.0,
        "last_time": "2026-03-12T10:30:00Z"
      }
    ]
  }
}
```

---

### 8.4 船舶详情

```
GET /api/vessels/{mmsi}
```

获取指定 MMSI 的船舶详细信息，包含最新位置。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| mmsi | int (路径) | ✅ | 合法 MMSI（100000000 ~ 999999999） |

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "mmsi": 366999000,
    "vessel_name": "EVER GIVEN",
    "imo": "9811000",
    "call_sign": "H3RC",
    "vessel_type": 70,
    "status": 0,
    "length": 400.0,
    "width": 59.0,
    "draft": 16.0,
    "last_position": {
      "longitude": -74.006,
      "latitude": 40.7128,
      "timestamp": "2026-03-12T10:30:00Z",
      "sog": 12.5,
      "cog": 45.0
    }
  }
}
```

---

### 8.5 轨迹查询

```
GET /api/vessels/{mmsi}/track?start_time={start}&end_time={end}
```

查询指定时间段内的船舶轨迹，使用 MobilityDB `atTime` 函数截取轨迹段。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| mmsi | int (路径) | ✅ | 合法 MMSI |
| start_time | datetime | ✅ | 起始时间（ISO 8601） |
| end_time | datetime | ✅ | 结束时间（ISO 8601） |

> 时间跨度不允许超过 `MAX_QUERY_DAYS`（默认 30 天）。

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "mmsi": 366999000,
    "vessel_name": "EVER GIVEN",
    "track": {
      "type": "LineString",
      "coordinates": [[-74.006, 40.712], [-74.010, 40.715]]
    },
    "timestamps": ["2026-03-12T08:00:00Z", "2026-03-12T08:05:00Z"],
    "point_count": 120
  }
}
```

---

### 8.6 航行统计

```
GET /api/vessels/{mmsi}/statistics?start_time={start}&end_time={end}
```

获取指定时间段内的航行统计信息，包括距离、时长、速度及速度时间序列。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| mmsi | int (路径) | ✅ | 合法 MMSI |
| start_time | datetime | ✅ | 起始时间（ISO 8601） |
| end_time | datetime | ✅ | 结束时间（ISO 8601） |

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "mmsi": 366999000,
    "distance_km": 156.8,
    "duration_hours": 12.5,
    "max_speed_knots": 22.3,
    "avg_speed_knots": 12.5,
    "speed_series": [
      { "time": "2026-03-12T08:00:00Z", "speed": 10.2 },
      { "time": "2026-03-12T08:05:00Z", "speed": 11.5 }
    ]
  }
}
```

---

### 8.7 区域检测

```
POST /api/analysis/area-detection
```

检测船舶在指定时间段内是否进入给定多边形区域。

**请求体（JSON）：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| mmsi | int | ✅ | 合法 MMSI（100000000 ~ 999999999） |
| start_time | datetime | ✅ | 起始时间 |
| end_time | datetime | ✅ | 结束时间 |
| area | GeoJSON object | ✅ | GeoJSON Polygon（type 必须为 "Polygon"） |

**请求示例：**
```json
{
  "mmsi": 366999000,
  "start_time": "2026-03-12T00:00:00Z",
  "end_time": "2026-03-12T23:59:59Z",
  "area": {
    "type": "Polygon",
    "coordinates": [[
      [-74.1, 40.6], [-73.9, 40.6],
      [-73.9, 40.8], [-74.1, 40.8],
      [-74.1, 40.6]
    ]]
  }
}
```

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "entered": true,
    "enter_time": "2026-03-12T14:30:00Z",
    "exit_time": "2026-03-12T16:45:00Z",
    "stay_duration_minutes": 135,
    "inside_track": {
      "type": "LineString",
      "coordinates": [[-74.05, 40.70], [-74.02, 40.72]]
    }
  }
}
```

---

### 8.8 两船距离

```
GET /api/analysis/distance?mmsi1={mmsi1}&mmsi2={mmsi2}&time={time}
```

计算两艘船舶的当前距离和历史最近距离。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| mmsi1 | int | ✅ | 第一艘船 MMSI（100000000 ~ 999999999） |
| mmsi2 | int | ✅ | 第二艘船 MMSI（不能与 mmsi1 相同） |
| time | datetime | ❌ | 指定时刻（ISO 8601），不传则使用当前时间 |

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "mmsi1": 366999000,
    "mmsi2": 367000000,
    "current_distance_km": 2.35,
    "min_distance_km": 0.82,
    "min_distance_time": "2026-03-12T09:15:00Z"
  }
}
```

---

### 8.9 轨迹预测

```
GET /api/vessels/{mmsi}/prediction?duration_minutes={minutes}
```

基于最近 10 个轨迹点的线性外推，预测船舶未来航行轨迹。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| mmsi | int (路径) | ✅ | — | 合法 MMSI |
| duration_minutes | int | ❌ | 60 | 预测时长（5~360 分钟） |

基于训练好的 `Mutual_Attention_opt` 模型，预测船舶未来航行轨迹。

**算法说明：**
1. 从数据库获取该船最近最多 120 个轨迹点
2. 将轨迹点重采样到模型输入长度（120 点）
3. 通过 ST 检索 + `Mutual_Attention_opt` 进行轨迹推理
4. 按接口参数 `duration_minutes / step_minutes` 对模型输出采样返回

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "mmsi": 366999000,
    "predicted_track": {
      "type": "LineString",
      "coordinates": [[-74.006, 40.712], [-74.015, 40.720]]
    },
    "predicted_timestamps": [
      "2026-03-12T12:00:00Z",
      "2026-03-12T12:05:00Z"
    ],
    "confidence": 0.85,
    "method": "mutual_attention_opt"
  }
}
```

---

## 9. Docker 部署

### 9.1 完整部署

```bash
cd ais-analyse-backend

# 设置数据库密码（可选，默认 ais_password）
export DB_PASSWORD=your_secure_password

# 启动所有服务
docker-compose up -d
```

**服务列表：**

| 服务 | 端口 | 说明 |
|------|------|------|
| db | 5432 | PostgreSQL 15 + MobilityDB 1.1 |
| api | 8000 | FastAPI 后端 |

### 9.2 仅启动数据库

本地开发时可仅启动数据库容器，API 由本地 uvicorn 运行：

```bash
docker-compose up -d db
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 9.3 数据持久化

数据库数据存储在 Docker volume `pgdata` 中。如需清除重置：

```bash
docker-compose down -v   # -v 删除 volume
docker-compose up -d db  # 重新初始化
```

---

## 10. 常见问题

### Q: 启动时报 `ModuleNotFoundError: No module named 'app'`

确保在 `ais-analyse-backend/` 目录下运行 uvicorn，而不是项目根目录。

### Q: 连接数据库失败

1. 确认数据库容器已启动：`docker-compose ps`
2. 确认 `.env` 中的 `DATABASE_URL` 与实际数据库地址一致
3. 如果数据库在容器中，主机应为 `localhost`（本地开发）或 `db`（Docker 网络）

### Q: 查询返回空数据

1. 确认已导入 AIS 数据到 `ais_raw` 表
2. 确认已执行 `SELECT build_vessel_trips();` 生成轨迹聚合表
3. 确认查询的 MMSI 和时间范围在数据集范围内

### Q: 轨迹预测返回 404

预测功能需要至少 2 个轨迹点。如果该船舶的历史记录过少，会返回"轨迹点不足，无法预测"。

### Q: CORS 跨域错误

在 `.env` 中配置 `CORS_ORIGINS` 包含前端的完整地址：

```env
CORS_ORIGINS=["http://localhost:5173","http://your-frontend-domain.com"]
```

### Q: 查询超时

- 确认 `vessels` 表已创建 `idx_vessels_mmsi` 索引
- 缩小查询时间范围（上限为 MAX_QUERY_DAYS 天）
- 对大数据集考虑增加数据库连接池大小

---

## 许可证

MIT
