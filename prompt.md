你现在是一名资深设计师，拥有丰富的全栈开发经验和极高的审美造诣，擅长现代化设计风格
我现在需要开发一个web网页,网页能够进行ais轨迹数据分析，包括
1. 显示船只，轨迹信息
2. 查询显示某个时间段轨迹
3. 计算显示行驶距离、时间、速度（最大，平均）
4. 查询显示船只是否进入某一区域
5. 计算两船之间的距离
6. 轨迹预测

要求现代化的 Web 前端界面，用于 AIS（Automatic Identification System）船舶轨迹数据分析系统，技术栈建议使用 Vue + TypeScript + ElementPlus + 地图组件，界面风格简洁、专业，类似海事监控平台.
后端采用fastAPI
数据库使用mobilityDB Github链接：https://github.com/MobilityDB/MobilityDB
建表sql如下CREATE TABLE ais_raw (
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
CREATE EXTENSION IF NOT EXISTS mobilitydb CASCADE;
-- 构建轨迹表：每个 MMSI 按时间聚合为轨迹，超过 1 小时无信号则切割为新轨迹段
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
    -- 去重：同一 mmsi 同一时刻只保留一条（取第一条）
    SELECT DISTINCT ON (mmsi, base_date_time)
        mmsi,
        vessel_name,
        longitude,
        latitude,
        base_date_time
    FROM ais_raw
    WHERE longitude IS NOT NULL
      AND latitude IS NOT NULL
    ORDER BY mmsi, base_date_time
) deduped
GROUP BY mmsi, vessel_name;

请你写一个完整的前端的可演示界面，可以使用一些假数据，请通过以下方式帮我完成界面的开发
1.用户体验部分：首先分析主要功能和用户需求，确定核心交互逻辑
2.产品界面规划：作为产品经理，定义关键界面，确保信息架构合理
3.高保真UI设计：元素尽量美观高级，使用现代化的设计风格
4.页面布局，左侧查询与分析控制面板。中间地图轨迹展示。右侧：分析结果与数据统计
5.HTML原型实现：使用HTML+TailwindCSS生成所有原型交界面，把设计存在目录下
6.思考过程仅仅思考功能需求，设计整体风格，仅在最终结果中输出代码
请按照以上要求生成完整的html代码，并确保可用于实际开发

将前端设计文档写入design-frontend.md文件中,后端设计文档写入design-backend.md中