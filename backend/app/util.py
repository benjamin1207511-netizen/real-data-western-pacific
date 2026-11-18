"""通用时间、地理、单位和解析工具。"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    dt = dt or now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        s = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def parse_epoch(value: Any) -> datetime | None:
    try:
        v = float(value)
        # 兼容毫秒时间戳（NMC 的 epoch 字段是毫秒）。
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, UTC)
    except Exception:
        return None


def parse_compact(value: str) -> datetime | None:
    """解析 YYYYMMDDHHMM / YYYYMMDDHH。"""
    value = (value or "").strip()
    for fmt in ("%Y%m%d%H%M", "%Y%m%d%H", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def fnum(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or s.lower() in {"null", "none", "nan", "m", "mm", "///", "////", "/////"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def knot_to_ms(value: float | None) -> float | None:
    return None if value is None else value * 0.514444


def kmh_to_ms(value: float | None) -> float | None:
    return None if value is None else value / 3.6


def rh_from_dewpoint(temp_c: float | None, dew_c: float | None) -> float | None:
    if temp_c is None or dew_c is None:
        return None
    try:
        a, b = 17.625, 243.04
        rh = 100.0 * math.exp((a * dew_c) / (b + dew_c) - (a * temp_c) / (b + temp_c))
        return round(clamp(rh, 0, 100), 1)
    except Exception:
        return None


def compass(deg: float | None) -> str:
    if deg is None:
        return "—"
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((deg % 360) / 22.5 + 0.5) % 16]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def destination(lat: float, lon: float, bearing: float, distance_km: float) -> tuple[float, float]:
    r = 6371.0088
    br = math.radians(bearing)
    dr = distance_km / r
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(dr) + math.cos(p1) * math.sin(dr) * math.cos(br))
    l2 = l1 + math.atan2(
        math.sin(br) * math.sin(dr) * math.cos(p1),
        math.cos(dr) - math.sin(p1) * math.sin(p2),
    )
    return math.degrees(p2), ((math.degrees(l2) + 540) % 360) - 180


def solar_elevation(lat: float, lon: float, dt: datetime) -> float:
    """粗略太阳高度角（度），用于判断可见光是否可用。"""
    day = dt.timetuple().tm_yday
    hour = dt.hour + dt.minute / 60 + dt.second / 3600
    decl = 23.44 * math.sin(math.radians(360 / 365 * (day - 81)))
    b = math.radians(360 / 364 * (day - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    true_solar = hour + (lon / 15.0) + eot / 60.0
    hour_angle = (true_solar - 12) * 15
    lat_r = math.radians(lat)
    dec_r = math.radians(decl)
    ha_r = math.radians(hour_angle)
    sin_el = math.sin(lat_r) * math.sin(dec_r) + math.cos(lat_r) * math.cos(dec_r) * math.cos(ha_r)
    return math.degrees(math.asin(clamp(sin_el, -1, 1)))


def clean_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def parse_jsonp(text: str) -> Any:
    text = text.strip()
    m = re.match(r"^[\w$.]+\((.*)\)\s*;?\s*$", text, re.S)
    if m:
        text = m.group(1).strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return json.loads(text)


def iso_hour(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:00:00Z")


def nearest_time(points: list[dict], target: datetime, max_gap_min: float = 45) -> dict | None:
    best = None
    best_gap = None
    for p in points:
        t = parse_iso(p.get("obs_time") or p.get("time"))
        if not t:
            continue
        gap = abs((t - target).total_seconds()) / 60
        if gap <= max_gap_min and (best_gap is None or gap < best_gap):
            best, best_gap = p, gap
    return best


def minutes_between(a: str | None, b: str | None) -> float | None:
    ta, tb = parse_iso(a), parse_iso(b)
    if not ta or not tb:
        return None
    return abs((ta - tb).total_seconds()) / 60


def ensure_dt(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return parse_iso(value) or parse_compact(value)
    return None


def utc_floor_10(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 10) * 10, second=0, microsecond=0)


def hours_ago(hours: float) -> datetime:
    return now_utc() - timedelta(hours=hours)
