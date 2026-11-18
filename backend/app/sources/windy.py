"""Windy 站点补充源（WMO / 机场 / PWS / MADIS / 船舶 / 浮标）。

Windy 的公开地图接口按坐标返回附近站点：
  https://node.windy.com/pois/v2/stations/{lat}/{lon}?count=100

它本身不是“官方观测 API”，而是 Windy 地图用于展示站点 POI 的接口。
这里只做小范围、带署名的补充：用 5° 网格查询、去重、过滤到
5–40N / 100–160E。使用方应遵守 Windy 的服务条款；如果 Windy 调整接口，
适配器会失败并在数据质量面板显示，不影响 METAR/SYNOP/NDBC。
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from typing import Any

import requests

from .. import config
from ..util import fnum, iso, now_utc

log = logging.getLogger(__name__)

BASE = "https://node.windy.com/pois/v2/stations/{lat}/{lon}"
HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Referer": "https://www.windy.com/",
    "Accept": "application/json",
}


def _grid() -> list[tuple[float, float]]:
    step = max(2.5, float(config.WINDY_GRID_STEP))
    lat = config.DOMAIN[1]
    out = []
    while lat <= config.DOMAIN[3]:
        lon = config.DOMAIN[0]
        while lon <= config.DOMAIN[2]:
            out.append((round(lat, 2), round(lon, 2)))
            lon += step
        lat += step
    return out


def _one(point: tuple[float, float]) -> list[dict[str, Any]]:
    lat, lon = point
    try:
        r = requests.get(
            BASE.format(lat=lat, lon=lon),
            params={"count": config.WINDY_COUNT},
            headers=HEADERS,
            timeout=25,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _parse_type(code: str) -> tuple[str, str | None, str | None]:
    """返回 station type, wmo_id, icao_id。"""
    prefix = code.split("-", 1)[0].lower()
    wmo = None
    icao = None
    if prefix == "wmo":
        m = re.search(r"wmo-(\d+)", code)
        wmo = m.group(1) if m else None
        return "land", wmo, None
    if prefix == "ad":
        m = re.search(r"ad-([A-Za-z]{4})", code)
        icao = m.group(1).upper() if m else None
        return "airport", None, icao
    if prefix == "ship":
        return "ship", None, None
    if prefix == "buoy":
        return "buoy", None, None
    if prefix == "pws":
        return "pws", None, None
    if prefix == "madis":
        return "pws", None, None
    return "land", None, None


def _age_minutes(row: dict[str, Any]) -> int | None:
    h = fnum(row.get("hAgo"))
    m = fnum(row.get("minAgo"))
    if h is None and m is None:
        return None
    return int((h or 0) * 60 + (m or 0))


def fetch() -> list[dict[str, Any]]:
    points = _grid()
    raw: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_one, p) for p in points]
        for fut in as_completed(futures):
            for row in fut.result():
                sid = row.get("id")
                if sid:
                    raw[str(sid)] = row
    now = now_utc()
    out = []
    for code, row in raw.items():
        lat, lon = fnum(row.get("lat")), fnum(row.get("lon"))
        if lat is None or lon is None:
            continue
        if not (config.DOMAIN[1] <= lat <= config.DOMAIN[3] and config.DOMAIN[0] <= lon <= config.DOMAIN[2]):
            continue
        age = _age_minutes(row)
        if age is not None and age > 240:
            continue
        stype, wmo, icao = _parse_type(code)
        obs_time = now - timedelta(minutes=max(0, age or 0))
        temp = fnum(row.get("temp"))
        dew = fnum(row.get("dew_point"))
        pressure = fnum(row.get("qnh"))
        wind = fnum(row.get("wind"))
        gust = fnum(row.get("gust"))
        rh = fnum(row.get("rh"))
        precip = fnum(row.get("precip"))
        precip_time = fnum(row.get("precip_time"))
        qc = ["windy"]
        if pressure is not None:
            qc.append("qnh")
        if age is not None and age > 120:
            qc.append("stale")
        if wind is not None and not 0 <= wind <= 90:
            wind = None
            qc.append("wind_range")
        if temp is not None and not -50 <= temp <= 55:
            temp = None
            qc.append("temp_range")
        out.append(
            {
                "station": {
                    "id": f"windy:{code}",
                    "wmo_id": wmo,
                    "icao_id": icao,
                    "name": row.get("name") or code,
                    "country": "",
                    "lat": lat,
                    "lon": lon,
                    "elev_m": None,
                    "type": stype,
                    "source": "WINDY",
                },
                "obs": {
                    "obs_time": iso(obs_time),
                    "source": "WINDY",
                    "wind_dir_deg": fnum(row.get("dir")),
                    "wind_ms": round(wind, 1) if wind is not None else None,
                    "gust_ms": round(gust, 1) if gust is not None else None,
                    "pressure_hpa": pressure,
                    "pressure_basis": "QNH" if pressure is not None else None,
                    "temp_c": temp,
                    "dewpoint_c": dew,
                    "rh_pct": rh,
                    "precip_1h_mm": precip if precip_time in (None, 1) else None,
                    "precip_3h_mm": precip if precip_time == 3 else None,
                    "precip_24h_mm": None,
                    "visibility_m": None,
                    "weather": "",
                    "clouds": "",
                    "qc_flags": qc,
                    "raw_json": row,
                },
            }
        )
    log.info("windy: %s points, %s raw, %s domain, %.1fs", len(points), len(raw), len(out), time.perf_counter() - started)
    return out
