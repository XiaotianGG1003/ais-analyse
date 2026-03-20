-- 启用 MobilityDB 扩展（自动包含 PostGIS）
CREATE EXTENSION IF NOT EXISTS mobilitydb CASCADE;

-- 原始 AIS 数据表
CREATE TABLE IF NOT EXISTS ais_raw (
    mmsi BIGINT,
    base_date_time TIMESTAMP,
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    sog DOUBLE PRECISION,
    cog DOUBLE PRECISION,
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

CREATE INDEX IF NOT EXISTS idx_ais_raw_mmsi ON ais_raw(mmsi);
CREATE INDEX IF NOT EXISTS idx_ais_raw_time ON ais_raw(base_date_time);
CREATE INDEX IF NOT EXISTS idx_ais_raw_mmsi_time ON ais_raw(mmsi, base_date_time);

-- 港口表（矩形区域）
CREATE TABLE IF NOT EXISTS ports (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    area_geom geometry(Polygon, 4326) NOT NULL,
    bbox_min_lon DOUBLE PRECISION NOT NULL,
    bbox_min_lat DOUBLE PRECISION NOT NULL,
    bbox_max_lon DOUBLE PRECISION NOT NULL,
    bbox_max_lat DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ports_area_geom ON ports USING GIST(area_geom);
CREATE INDEX IF NOT EXISTS idx_ports_name ON ports(name);

-- 轨迹聚合表（导入数据后执行）
-- 使用函数封装，可在数据导入后调用 SELECT build_vessel_trips();
CREATE OR REPLACE FUNCTION build_vessel_trips() RETURNS void AS $$
BEGIN
    DROP TABLE IF EXISTS vessels;
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

    CREATE INDEX idx_vessels_mmsi ON vessels(mmsi);
END;
$$ LANGUAGE plpgsql;
