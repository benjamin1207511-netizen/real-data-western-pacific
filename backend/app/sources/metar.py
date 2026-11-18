"""Aviation Weather Center METAR 区域查询。

  https://aviationweather.gov/api/data/metar?bbox=5,100,40,160&format=json&hours=3

注意 bbox 顺序是 minLat,minLon,maxLat,maxLon。
"""

from __future__ import annotations

import logging
from typing import Any

from ..http import fetch_json
from ..util import fnum, iso, knot_to_ms, parse_epoch, rh_from_dewpoint

log = logging.getLogger(__name__)

URL = "https://aviationweather.gov/api/data/metar"
BBOX = "5,100,40,160"


def _qc(rec: dict[str, Any]) -> list[str]:
    flags = []
    if rec["temp_c"] is not None and not -50 <= rec["temp_c"] <= 55:
        flags.append("temp_range")
    if rec["wind_ms"] is not None and not 0 <= rec["wind_ms"] <= 120:
        flags.append("wind_range")
    if rec["pressure_hpa"] is not None and not 850 <= rec["pressure_hpa"] <= 1100:
        flags.append("pressure_range")
    if rec["pressure_basis"] == "QNH":
        flags.append("qnh")
    return flags


def fetch() -> list[dict[str, Any]]:
    data = fetch_json(
        URL,
        params={"bbox": BBOX, "format": "json", "hours": "3"},
        timeout=45,
        retries=3,
    )
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        lat, lon = fnum(row.get("lat")), fnum(row.get("lon"))
        icao = (row.get("icaoId") or "").strip()
        if lat is None or lon is None or not icao:
            continue
        dt = parse_epoch(row.get("obsTime"))
        if not dt:
            continue
        temp = fnum(row.get("temp"))
        dew = fnum(row.get("dewp"))
        wind = knot_to_ms(fnum(row.get("wspd")))
        gust = knot_to_ms(fnum(row.get("wgst")))
        wdir_raw = row.get("wdir")
        wdir = fnum(wdir_raw)
        pressure = fnum(row.get("slp"))
        basis = "MSLP" if pressure is not None else None
        if pressure is None:
            pressure = fnum(row.get("altim"))
            basis = "QNH" if pressure is not None else None
        rec = {
            "station": {
                "id": f"metar:{icao}",
                "icao_id": icao,
                "wmo_id": None,
                "name": row.get("name") or icao,
                "country": "",
                "lat": lat,
                "lon": lon,
                "elev_m": fnum(row.get("elev")),
                "type": "airport",
                "source": "METAR",
            },
            "obs": {
                "obs_time": iso(dt),
                "source": "METAR",
                "wind_dir_deg": wdir,
                "wind_ms": round(wind, 1) if wind is not None else None,
                "gust_ms": round(gust, 1) if gust is not None else None,
                "pressure_hpa": pressure,
                "pressure_basis": basis,
                "temp_c": temp,
                "dewpoint_c": dew,
                "rh_pct": rh_from_dewpoint(temp, dew),
                "precip_1h_mm": fnum(row.get("precip")),
                "precip_3h_mm": None,
                "precip_24h_mm": None,
                "visibility_m": None,
                "weather": row.get("wxString") or "",
                "clouds": str(row.get("clouds") or ""),
                "raw_json": row,
            },
        }
        rec["obs"]["qc_flags"] = _qc(rec["obs"])
        out.append(rec)
    return out
