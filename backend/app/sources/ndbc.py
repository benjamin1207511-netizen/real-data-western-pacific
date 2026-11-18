"""NOAA NDBC 浮标/固定平台实时观测。

  https://www.ndbc.noaa.gov/activestations.xml
  https://www.ndbc.noaa.gov/data/realtime2/{station}.txt

只取范围内且 met=y 的站；数据列：
YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from .. import config
from ..http import fetch_text
from ..util import fnum, iso, rh_from_dewpoint

log = logging.getLogger(__name__)

STATIONS_URL = "https://www.ndbc.noaa.gov/activestations.xml"
DATA_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station}.txt"


def fetch_station_list() -> list[dict[str, Any]]:
    xml = fetch_text(STATIONS_URL, timeout=45, retries=2)
    root = ET.fromstring(xml)
    out = []
    for node in root.findall("station"):
        try:
            lat = float(node.get("lat"))
            lon = float(node.get("lon"))
        except Exception:
            continue
        west, south, east, north = config.DOMAIN
        if not (south <= lat <= north and west <= lon <= east):
            continue
        if (node.get("met") or "").lower() != "y":
            continue
        out.append(
            {
                "id": node.get("id"),
                "lat": lat,
                "lon": lon,
                "name": node.get("name") or node.get("id"),
                "type": node.get("type") or "buoy",
                "owner": node.get("owner") or "NDBC",
            }
        )
    return out


def parse_obs(station: dict[str, Any], text: str) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return []
    out = []
    for line in lines[2:]:
        parts = line.split()
        if len(parts) < 18:
            continue
        try:
            dt = datetime(
                int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]),
                tzinfo=timezone.utc,
            )
        except Exception:
            continue
        wdir = fnum(parts[5])
        wspd = fnum(parts[6])
        gust = fnum(parts[7])
        pres = fnum(parts[12])
        temp = fnum(parts[13])
        dewp = fnum(parts[15])
        if all(v is None for v in (wdir, wspd, pres, temp)):
            continue
        qc = []
        if pres is not None and not 850 <= pres <= 1100:
            qc.append("pressure_range")
        if temp is not None and not -50 <= temp <= 55:
            qc.append("temp_range")
        out.append(
            {
                "station": {
                    "id": f"ndbc:{station['id']}",
                    "wmo_id": None,
                    "icao_id": None,
                    "name": station["name"],
                    "country": "US/Pacific",
                    "lat": station["lat"],
                    "lon": station["lon"],
                    "elev_m": 0,
                    "type": "buoy" if station["type"] == "buoy" else "platform",
                    "source": "NDBC",
                },
                "obs": {
                    "obs_time": iso(dt),
                    "source": "NDBC",
                    "wind_dir_deg": wdir,
                    "wind_ms": round(wspd, 1) if wspd is not None else None,
                    "gust_ms": round(gust, 1) if gust is not None else None,
                    "pressure_hpa": pres,
                    "pressure_basis": "MSLP",
                    "temp_c": temp,
                    "dewpoint_c": dewp,
                    "rh_pct": rh_from_dewpoint(temp, dewp),
                    "precip_1h_mm": None,
                    "precip_3h_mm": None,
                    "precip_24h_mm": None,
                    "visibility_m": None,
                    "weather": "",
                    "clouds": "",
                    "qc_flags": qc,
                    "raw_json": {"line": line},
                },
            }
        )
    return out


def fetch(hours: int = 72) -> list[dict[str, Any]]:
    stations = fetch_station_list()
    out: list[dict[str, Any]] = []

    def one(st: dict[str, Any]):
        try:
            text = fetch_text(DATA_URL.format(station=st["id"]), timeout=30, retries=2)
            return parse_obs(st, text)[-min(hours, 72):]
        except Exception as exc:  # noqa: BLE001
            log.debug("NDBC %s failed: %s", st["id"], exc)
            return []

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(one, st) for st in stations]
        for f in as_completed(futures):
            try:
                out.extend(f.result())
            except Exception:
                continue
    # 去重
    unique = {}
    for item in out:
        unique[(item["station"]["id"], item["obs"]["obs_time"])] = item
    return list(unique.values())
