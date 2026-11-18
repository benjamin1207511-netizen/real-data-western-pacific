"""NOAA Global Drifter Program 活动漂流浮标。

数据页：
  https://out-gdpsio.ucsd.edu/apps/projects/noaa/global-drifter-program.html

页面实际使用：
  https://out-gdpsio.ucsd.edu/apps/files/geojson/gdp-active.geojson

GeoJSON 每个点包含 WMO、更新时间、SST，部分浮标还有 SLP、有效波高、
波周期、气温和风向。这里把它们作为“漂流浮标站”接入，只保留
5–40N / 100–160E 内的活动浮标。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from .. import config
from ..http import fetch_json
from ..util import fnum, iso

log = logging.getLogger(__name__)

URL = "https://out-gdpsio.ucsd.edu/apps/files/geojson/gdp-active.geojson"


def _fields(desc: str) -> dict[str, str]:
    text = desc.replace("</br>", "\n").replace("<br/>", "\n").replace("<br>", "\n")
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    return out


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(m.group(0)) if m else None


def fetch() -> list[dict[str, Any]]:
    data = fetch_json(URL, timeout=40, retries=3)
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        return []
    out = []
    for f in features:
        try:
            lon, lat = f["geometry"]["coordinates"][:2]
            lat, lon = float(lat), float(lon)
        except Exception:
            continue
        if not (config.DOMAIN[1] <= lat <= config.DOMAIN[3] and config.DOMAIN[0] <= lon <= config.DOMAIN[2]):
            continue
        props = f.get("properties") or {}
        fields = _fields(str(props.get("description") or ""))
        wmo = (fields.get("WMO") or props.get("Name") or "").strip()
        if not wmo:
            continue
        dt = None
        ts = (fields.get("TS") or "").replace("UTC", "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S",):
            try:
                dt = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
                break
            except Exception:
                continue
        if dt is None:
            continue
        sst = _number(fields.get("SST"))
        slp = _number(fields.get("SLP"))
        air = _number(fields.get("Ta"))
        wdir = _number(fields.get("Dd"))
        qc = ["drifter"]
        if sst is not None and not -5 <= sst <= 40:
            sst = None
            qc.append("sst_range")
        if slp is not None and not 850 <= slp <= 1100:
            slp = None
            qc.append("pressure_range")
        out.append(
            {
                "station": {
                    "id": f"gdp:{wmo}",
                    "wmo_id": wmo,
                    "icao_id": None,
                    "name": f"Drifter {wmo}",
                    "country": "OCEAN",
                    "lat": lat,
                    "lon": lon,
                    "elev_m": 0,
                    "type": "drifter",
                    "source": "GDP",
                },
                "obs": {
                    "obs_time": iso(dt),
                    "source": "GDP",
                    "wind_dir_deg": wdir,
                    "wind_ms": None,
                    "gust_ms": None,
                    "pressure_hpa": slp,
                    "pressure_basis": "MSLP" if slp is not None else None,
                    "temp_c": air,
                    "dewpoint_c": None,
                    "rh_pct": None,
                    "sea_temp_c": sst,
                    "precip_1h_mm": None,
                    "precip_3h_mm": None,
                    "precip_24h_mm": None,
                    "visibility_m": None,
                    "weather": "drifter",
                    "clouds": "",
                    "qc_flags": qc,
                    "raw_json": {"feature": f, "fields": fields},
                },
            }
        )
    log.info("GDP drifters: %s in domain", len(out))
    return out
