"""JMA RSMC 东京台风数据（备用/交叉校验）。

  https://www.jma.go.jp/bosai/typhoon/data/targetTc.json
  https://www.jma.go.jp/bosai/typhoon/data/{TC}/forecast.json

JMA 的 forecast.json 主要提供位置、概率圈和预报，不提供中心气压/风速，
所以只用作路径交叉校验；强度仍以 CMA 为主。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from ..http import fetch_json
from ..util import fnum, iso, parse_iso

log = logging.getLogger(__name__)

TARGET_URL = "https://www.jma.go.jp/bosai/typhoon/data/targetTc.json"
FORECAST_URL = "https://www.jma.go.jp/bosai/typhoon/data/{tc}/forecast.json"


def fetch_targets() -> list[dict[str, Any]]:
    data = fetch_json(TARGET_URL, timeout=25, retries=2)
    return data if isinstance(data, list) else []


def _center(item: dict[str, Any]) -> tuple[float, float] | None:
    c = item.get("center")
    if isinstance(c, list) and len(c) >= 2:
        lat, lon = fnum(c[0]), fnum(c[1])
        if lat is not None and lon is not None:
            return lat, lon
    return None


def parse_forecast(data: list[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(data, list) or not data:
        return None
    title = next((x for x in data if x.get("part") == "title"), None)
    if not title:
        return None
    tc = target.get("tropicalCyclone")
    number = str(title.get("typhoonNumber") or target.get("typhoonNumber") or "")
    name_en = (title.get("name") or {}).get("en", "")
    name_jp = (title.get("name") or {}).get("jp", "")
    points = []
    forecasts = []
    for item in data:
        if item.get("part") == "title":
            continue
        center = _center(item)
        if not center:
            continue
        vt = (item.get("validtime") or {}).get("UTC")
        dt = parse_iso(vt)
        if not dt:
            continue
        hours = item.get("advancedHours") or 0
        circle = (item.get("probabilityCircle") or {}).get("radius")
        rec = {
            "valid_time": iso(dt),
            "lat": center[0],
            "lon": center[1],
            "pressure_hpa": None,
            "wind_ms": None,
            "category": target.get("category") or "",
            "wind_radii": {},
            "forecast": 1 if hours else 0,
            "probability_radius_m": fnum(circle),
            "issued_at": iso(dt - timedelta(hours=int(hours))),
        }
        if hours:
            forecasts.append(rec)
        else:
            points.append(rec)
    if not points and not forecasts:
        return None
    allpts = points + forecasts
    last = points[-1] if points else forecasts[0]
    return {
        "id": f"jma:{number}",
        "agency": "JMA",
        "season": int(last["valid_time"][:4]) if last["valid_time"][:4].isdigit() else None,
        "number": number,
        "name_en": name_en,
        "name_zh": name_jp,
        "status": "start",
        "category": target.get("category") or "",
        "lat": last["lat"],
        "lon": last["lon"],
        "pressure_hpa": None,
        "wind_ms": None,
        "motion_dir": None,
        "motion_speed_kmh": None,
        "observed_at": last["valid_time"],
        "updated_at": iso(parse_iso((title.get("issue") or {}).get("UTC"))),
        "points": points,
        "forecasts": forecasts,
        "raw": {"target": target, "title": title},
    }


def fetch_active() -> list[dict[str, Any]]:
    out = []
    for target in fetch_targets():
        tc = target.get("tropicalCyclone")
        if not tc:
            continue
        try:
            data = fetch_json(FORECAST_URL.format(tc=tc), timeout=25, retries=2)
            parsed = parse_forecast(data, target)
            if parsed:
                out.append(parsed)
        except Exception as exc:  # noqa: BLE001
            log.warning("JMA %s failed: %s", tc, exc)
    return out
