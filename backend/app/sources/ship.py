"""船舶 / 海上平台观测。

当前可用的公开源里，CMA 台风网的 shipStation 接口经常为空；本适配器会把
接口返回的船舶报归一化。如果为空，数据质量面板会显示“当前无船舶报”，
不会用模式值冒充。
"""

from __future__ import annotations

import logging
from typing import Any

from ..http import fetch_text
from ..util import fnum, iso, parse_compact, parse_jsonp, rh_from_dewpoint

log = logging.getLogger(__name__)

URL = "https://typhoon.nmc.cn/weatherservice/shipStation/view/default"


def _unwrap(text: str) -> Any:
    import re

    text = text.strip()
    m = re.search(r"^[\w$.]+\((.*)\)\s*;?\s*$", text, re.S)
    if m:
        text = m.group(1).strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return parse_jsonp(text)


def fetch() -> list[dict[str, Any]]:
    try:
        data = _unwrap(fetch_text(URL, timeout=25, retries=2))
    except Exception as exc:  # noqa: BLE001
        log.debug("ship feed failed: %s", exc)
        return []
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        if not isinstance(row, dict):
            continue
        lat, lon = fnum(row.get("lat") or row.get("latitude")), fnum(row.get("lon") or row.get("longitude"))
        if lat is None or lon is None:
            continue
        dt = parse_compact(str(row.get("time") or row.get("datetime") or ""))
        if not dt:
            continue
        temp = fnum(row.get("temp") or row.get("airtemp"))
        dew = fnum(row.get("dewp") or row.get("dewpoint"))
        out.append(
            {
                "station": {
                    "id": f"ship:{row.get('id') or row.get('name') or f'{lat:.2f}_{lon:.2f}'}",
                    "wmo_id": None,
                    "icao_id": None,
                    "name": row.get("name") or row.get("id") or "Ship",
                    "country": "",
                    "lat": lat,
                    "lon": lon,
                    "elev_m": 0,
                    "type": "ship",
                    "source": "SHIP",
                },
                "obs": {
                    "obs_time": iso(dt),
                    "source": "SHIP",
                    "wind_dir_deg": fnum(row.get("wd") or row.get("wind_dir")),
                    "wind_ms": fnum(row.get("ws") or row.get("wind_speed")),
                    "gust_ms": fnum(row.get("gust")),
                    "pressure_hpa": fnum(row.get("pres") or row.get("pressure") or row.get("slp")),
                    "pressure_basis": "MSLP",
                    "temp_c": temp,
                    "dewpoint_c": dew,
                    "rh_pct": rh_from_dewpoint(temp, dew),
                    "precip_1h_mm": fnum(row.get("rain")),
                    "precip_3h_mm": None,
                    "precip_24h_mm": None,
                    "visibility_m": None,
                    "weather": "",
                    "clouds": "",
                    "qc_flags": [],
                    "raw_json": row,
                },
            }
        )
    return out
