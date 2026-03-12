import math


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """计算两点间的 Haversine 距离（千米）"""
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def ms_to_knots(speed_ms: float) -> float:
    """m/s 转 节"""
    return speed_ms * 1.94384


def meters_to_km(meters: float) -> float:
    """米 转 千米"""
    return meters / 1000.0
