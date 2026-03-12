# AIS 船舶轨迹分析系统 — 后端设计文档

## 1. 系统架构

```
┌─────────────┐     HTTP/JSON      ┌──────────────┐      SQL       ┌───────────────┐
│  Vue 前端    │ ◄──────────────► │  FastAPI      │ ◄────────────► │  PostgreSQL    │
│  (浏览器)    │                   │  后端服务     │                │  + MobilityDB  │
└─────────────┘                   └──────────────┘                └───────────────┘
```

### 1.1 技术栈

| 类目 | 选型 | 版本 | 说明 |
|------|------|------|------|
| Web 框架 | FastAPI | 0.100+ | 高性能异步 API 框架 |
| ORM/DB | asyncpg + SQLAlchemy | — | 异步数据库驱动 |
| 数据库 | PostgreSQL + MobilityDB | PG15 + MDB1.1 | 时空轨迹数据库扩展 |
| 数据验证 | Pydantic v2 | 2.x | 请求/响应模型 |
| 空间计算 | PostGIS + MobilityDB | — | 空间查询与轨迹分析 |
| 缓存 | Redis（可选） | — | 热点数据缓存 |
| 部署 | Docker Compose | — | 容器化部署 |

## 2. 数据库设计

### 2.1 数据表结构

#### ais_raw — 原始 AIS 数据表
```sql
CREATE TABLE ais_raw (
    mmsi BIGINT,
    base_date_time TIMESTAMP,
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    sog DOUBLE PRECISION,          -- Speed Over Ground (节)
    cog DOUBLE PRECISION,          -- Course Over Ground (度)
    heading TEXT,
    vessel_name TEXT,
    imo TEXT,
    call_sign TEXT,
    vessel_type INT,
    status INT,
    length DOUBLE PRECISION,
    width DOUBLE PRECISION,
    draft DOUBLE PRECISION,
    cargo INT,
    transceiver TEXT
);

-- 索引
CREATE INDEX idx_ais_raw_mmsi ON ais_raw(mmsi);
CREATE INDEX idx_ais_raw_time ON ais_raw(base_date_time);
CREATE INDEX idx_ais_raw_mmsi_time ON ais_raw(mmsi, base_date_time);
```

#### vessels — 轨迹聚合表
```sql
CREATE EXTENSION IF NOT EXISTS mobilitydb CASCADE;

CREATE TABLE vessels AS
SELECT
    mmsi,
    vessel_name,
    tgeogpointSeqSetGaps(
        array_agg(
            tgeogpoint(
                ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography,
                base_date_time
            ) ORDER BY base_date_time
        ),
        interval '1 hour'
    ) AS trip
FROM (
    SELECT DISTINCT ON (mmsi, base_date_time)
        mmsi, vessel_name, longitude, latitude, base_date_time
    FROM ais_raw
    WHERE longitude IS NOT NULL AND latitude IS NOT NULL
    ORDER BY mmsi, base_date_time
) deduped
GROUP BY mmsi, vessel_name;

-- 索引
CREATE INDEX idx_vessels_mmsi ON vessels(mmsi);
```

### 2.2 MobilityDB 核心函数

| 函数 | 用途 | 示例 |
|------|------|------|
| `atTime(trip, tstzspan)` | 截取时间段轨迹 | 时间段查询 |
| `length(trip)` | 计算轨迹长度（米） | 行驶距离 |
| `duration(trip)` | 计算轨迹时长 | 行驶时间 |
| `speed(trip)` | 计算速度序列 | 速度变化 |
| `maxValue(speed(trip))` | 最大速度 | 最大速度 |
| `twAvg(speed(trip))` | 时间加权平均速度 | 平均速度 |
| `eIntersects(trip, geo)` | 轨迹是否与区域相交 | 区域检测 |
| `nearestApproachDistance` | 最近接近距离 | 两船距离 |
| `atGeometry(trip, geo)` | 截取区域内轨迹 | 区域检测详情 |

## 3. API 设计

### 3.1 项目结构

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 应用入口
│   ├── config.py             # 配置管理
│   ├── database.py           # 数据库连接
│   ├── models/
│   │   ├── __init__.py
│   │   ├── vessel.py         # 船舶数据模型
│   │   └── analysis.py       # 分析结果模型
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── vessels.py        # 船舶相关 API
│   │   └── analysis.py       # 分析功能 API
│   ├── services/
│   │   ├── __init__.py
│   │   ├── vessel_service.py # 船舶业务逻辑
│   │   └── analysis_service.py # 分析业务逻辑
│   └── utils/
│       ├── __init__.py
│       └── geo.py            # 地理计算工具
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

### 3.2 API 接口详细设计

#### 3.2.1 船舶搜索
```
GET /api/vessels/search?keyword={keyword}&limit={limit}
```

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| keyword | string | 是 | MMSI 或船名关键字 |
| limit | int | 否 | 返回数量限制，默认 20 |

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

#### 3.2.2 船舶详情
```
GET /api/vessels/{mmsi}
```

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
      "longitude": -74.0060,
      "latitude": 40.7128,
      "timestamp": "2026-03-12T10:30:00Z",
      "sog": 12.5,
      "cog": 45.0
    }
  }
}
```

#### 3.2.3 轨迹查询
```
GET /api/vessels/{mmsi}/track?start_time={start}&end_time={end}
```

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| start_time | datetime | 是 | 起始时间 ISO 8601 |
| end_time | datetime | 是 | 结束时间 ISO 8601 |

**后端 SQL：**
```sql
SELECT mmsi, vessel_name,
       ST_AsGeoJSON(trajectory(atTime(trip, tstzspan(%s, %s))))::json AS geojson
FROM vessels
WHERE mmsi = %s;
```

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "mmsi": 366999000,
    "vessel_name": "EVER GIVEN",
    "track": {
      "type": "LineString",
      "coordinates": [[-74.006, 40.712], [-74.010, 40.715], ...]
    },
    "timestamps": ["2026-03-12T08:00:00Z", "2026-03-12T08:05:00Z", ...],
    "point_count": 120
  }
}
```

#### 3.2.4 航行统计
```
GET /api/vessels/{mmsi}/statistics?start_time={start}&end_time={end}
```

**后端 SQL：**
```sql
SELECT
    mmsi,
    length(atTime(trip, tstzspan(%s, %s))) AS distance_m,
    duration(atTime(trip, tstzspan(%s, %s))) AS duration,
    maxValue(speed(atTime(trip, tstzspan(%s, %s)))) AS max_speed,
    twAvg(speed(atTime(trip, tstzspan(%s, %s)))) AS avg_speed
FROM vessels
WHERE mmsi = %s;
```

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
      {"time": "2026-03-12T08:00:00Z", "speed": 10.2},
      {"time": "2026-03-12T08:05:00Z", "speed": 11.5}
    ]
  }
}
```

#### 3.2.5 区域检测
```
POST /api/analysis/area-detection
```

**请求体：**
```json
{
  "mmsi": 366999000,
  "start_time": "2026-03-12T00:00:00Z",
  "end_time": "2026-03-12T23:59:59Z",
  "area": {
    "type": "Polygon",
    "coordinates": [[[-74.1, 40.6], [-73.9, 40.6], [-73.9, 40.8], [-74.1, 40.8], [-74.1, 40.6]]]
  }
}
```

**后端 SQL：**
```sql
-- 检测是否进入区域
SELECT eIntersects(
    atTime(trip, tstzspan(%s, %s)),
    ST_GeogFromText(%s)
) AS entered
FROM vessels WHERE mmsi = %s;

-- 获取区域内轨迹段
SELECT ST_AsGeoJSON(trajectory(atGeometry(
    atTime(trip, tstzspan(%s, %s)),
    ST_GeomFromGeoJSON(%s)
)))::json AS inside_track
FROM vessels WHERE mmsi = %s;
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
      "coordinates": [...]
    }
  }
}
```

#### 3.2.6 两船距离
```
GET /api/analysis/distance?mmsi1={mmsi1}&mmsi2={mmsi2}&time={time}
```

**后端 SQL：**
```sql
SELECT nearestApproachDistance(v1.trip, v2.trip) AS min_distance_m,
       nearestApproachInstant(v1.trip, v2.trip) AS closest_time
FROM vessels v1, vessels v2
WHERE v1.mmsi = %s AND v2.mmsi = %s;

-- 指定时刻距离
SELECT ST_Distance(
    valueAtTimestamp(v1.trip, %s),
    valueAtTimestamp(v2.trip, %s)
) AS distance_m
FROM vessels v1, vessels v2
WHERE v1.mmsi = %s AND v2.mmsi = %s;
```

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

#### 3.2.7 轨迹预测
```
GET /api/vessels/{mmsi}/prediction?duration_minutes={minutes}
```

**算法说明：**
基于最近 N 个轨迹点，使用线性外推或卡尔曼滤波预测未来位置。

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "mmsi": 366999000,
    "predicted_track": {
      "type": "LineString",
      "coordinates": [[-74.006, 40.712], [-74.015, 40.720], ...]
    },
    "predicted_timestamps": ["2026-03-12T12:00:00Z", "2026-03-12T12:30:00Z"],
    "confidence": 0.85,
    "method": "linear_extrapolation"
  }
}
```

#### 3.2.8 船舶列表
```
GET /api/vessels?page={page}&page_size={size}&vessel_type={type}
```

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
        "last_time": "2026-03-12T10:30:00Z"
      }
    ]
  }
}
```

## 4. 核心业务逻辑

### 4.1 Pydantic 模型

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class VesselBase(BaseModel):
    mmsi: int
    vessel_name: Optional[str] = None
    vessel_type: Optional[int] = None
    length: Optional[float] = None
    width: Optional[float] = None

class TrackQuery(BaseModel):
    start_time: datetime
    end_time: datetime

class AreaDetectionRequest(BaseModel):
    mmsi: int
    start_time: datetime
    end_time: datetime
    area: dict  # GeoJSON Polygon

class TrackStatistics(BaseModel):
    distance_km: float
    duration_hours: float
    max_speed_knots: float
    avg_speed_knots: float

class DistanceResult(BaseModel):
    current_distance_km: float
    min_distance_km: float
    min_distance_time: Optional[datetime] = None

class PredictionResult(BaseModel):
    predicted_track: dict  # GeoJSON
    confidence: float
    method: str
```

### 4.2 轨迹预测算法

```python
import numpy as np

def linear_extrapolation(track_points, duration_minutes=60, step_minutes=5):
    """
    基于最近轨迹点的线性外推预测
    """
    recent = track_points[-10:]  # 取最近10个点
    
    # 计算平均速度和航向
    lons = [p['longitude'] for p in recent]
    lats = [p['latitude'] for p in recent]
    
    # 线性回归拟合趋势
    t = np.arange(len(recent))
    lon_slope = np.polyfit(t, lons, 1)[0]
    lat_slope = np.polyfit(t, lats, 1)[0]
    
    # 外推
    steps = duration_minutes // step_minutes
    predictions = []
    for i in range(1, steps + 1):
        predictions.append({
            'longitude': lons[-1] + lon_slope * i,
            'latitude': lats[-1] + lat_slope * i,
        })
    
    return predictions
```

## 5. 安全设计

### 5.1 输入验证
- 所有 API 参数通过 Pydantic 模型验证
- MMSI 必须为合法整数范围（100000000-999999999）
- 时间范围限制最大查询跨度（如 30 天）
- GeoJSON 区域验证合法性

### 5.2 SQL 注入防护
- 全部使用参数化查询（`%s` 占位符）
- 禁止字符串拼接 SQL

### 5.3 CORS 配置
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # 前端开发地址
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 5.4 速率限制
- 对高消耗查询（区域检测、轨迹预测）设置速率限制
- 使用 slowapi 或自定义中间件

## 6. 部署方案

### 6.1 Docker Compose

```yaml
version: '3.8'
services:
  db:
    image: mobilitydb/mobilitydb:pg15-develop
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: ais_db
      POSTGRES_USER: ais_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      
  api:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://ais_user:${DB_PASSWORD}@db:5432/ais_db
    depends_on:
      - db

  frontend:
    build: ./frontend
    ports:
      - "80:80"
    depends_on:
      - api

volumes:
  pgdata:
```

## 7. 性能优化

- 对 vessels 表的 trip 列创建 SP-GiST 索引加速时空查询
- 对高频查询的船舶轨迹做 Redis 缓存
- 大时间范围查询采用分段加载
- 使用 asyncpg 异步驱动避免阻塞
- 轨迹点下采样（Douglas-Peucker）减少前端渲染压力
