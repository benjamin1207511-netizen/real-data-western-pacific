"""CMA / 中央气象台台风网（NMC）实时台风数据。

接口返回 JSONP：
  https://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default
  https://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{id}

数据结构（view）：
  typhoon = [id, name_en, name_cn, no, no2, ?, ?, status, points, ...]
  point   = [id, time, epoch, grade, lon, lat, pressure, wind,
             move_dir, move_speed, radii, forecasts, issue]
  radii   = [["30KTS", ne, se, sw, nw, point_id], ...]
  forecasts = {"BABJ": [[hour, base, lon, lat, pressure, wind, agency, grade], ...]}
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from ..http import fetch_text
from ..util import fnum, iso, parse_compact, parse_epoch, parse_jsonp

log = logging.getLogger(__name__)

BASE = "https://typhoon.nmc.cn/weatherservice/typhoon/jsons"
LIST_URL = f"{BASE}/list_default"
DETAIL_URL = f"{BASE}/view_{{id}}"


def _unwrap(text: str) -> Any:
    text = text.strip()
    m = re.search(r"^[\w$.]+\((.*)\)\s*;?\s*$", text, re.S)
    if m:
        text = m.group(1).strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return parse_jsonp(text)


def _parse_radii(raw: Any) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, list) or len(item) < 5:
            continue
        level = str(item[0]).upper().replace("KTS", "").strip()
        if not level.isdigit():
            continue
        vals = [fnum(item[1]), fnum(item[2]), fnum(item[3]), fnum(item[4])]
        if any(v is None for v in vals):
            continue
        out[level] = [float(v) for v in vals]  # NE, SE, SW, NW
    return out


def parse_list(text: str) -> list[dict[str, Any]]:
    data = _unwrap(text)
    rows = data.get("typhoonList") or []
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 8:
            continue
        out.append(
            {
                "id": str(row[0]),
                "name_en": row[1] or "",
                "name_zh": row[2] or row[6] or "",
                "number": str(row[3] or row[4] or ""),
                "status": row[7] or "",
            }
        )
    return out


def parse_detail(text: str) -> dict[str, Any] | None:
    data = _unwrap(text)
    t = data.get("typhoon")
    if not isinstance(t, list) or len(t) < 9:
        return None
    points_raw = t[8] or []
    if not isinstance(points_raw, list) or not points_raw:
        return None

    points: list[dict[str, Any]] = []
    for p in points_raw:
        if not isinstance(p, list) or len(p) < 8:
            continue
        # NMC 的 YYYYMMDDHHMM 是北京时（UTC+8）；优先用 epoch 字段。
        dt = parse_epoch(p[2]) or (
            parse_compact(str(p[1])) - timedelta(hours=8) if parse_compact(str(p[1])) else None
        )
        if not dt:
            continue
        lat, lon = fnum(p[5]), fnum(p[4])
        if lat is None or lon is None:
            continue
        points.append(
            {
                "valid_time": iso(dt),
                "lat": lat,
                "lon": lon,
                "pressure_hpa": fnum(p[6]),
                "wind_ms": fnum(p[7]),
                "category": p[3] or "",
                "motion_dir": p[8] or "",
                "motion_speed_kmh": fnum(p[9]),
                "wind_radii": _parse_radii(p[10] if len(p) > 10 else None),
                "forecast": 0,
            }
        )
    if not points:
        return None

    last = points[-1]
    raw_forecasts = points_raw[-1][11] if len(points_raw[-1]) > 11 else {}
    forecasts: list[dict[str, Any]] = []
    if isinstance(raw_forecasts, dict):
        for agency, rows in raw_forecasts.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                hour, base, lon, lat, pressure, wind = row[:6]
                dt = parse_compact(str(base))
                if not dt:
                    continue
                # base 也是北京时。
                dt = dt - timedelta(hours=8)
                try:
                    dt = dt + timedelta(hours=int(hour))
                except Exception:
                    continue
                lon, lat = fnum(lon), fnum(lat)
                if lat is None or lon is None:
                    continue
                forecasts.append(
                    {
                        "valid_time": iso(dt),
                        "lat": lat,
                        "lon": lon,
                        "pressure_hpa": fnum(pressure),
                        "wind_ms": fnum(wind),
                        "category": row[7] if len(row) > 7 else "",
                        "agency": agency,
                        "forecast": 1,
                        "issued_at": iso(dt - timedelta(hours=int(hour))),
                    }
                )

    observed = points[-1]["valid_time"]
    season = int(observed[:4]) if observed[:4].isdigit() else None
    issue_raw = points_raw[-1][12] if len(points_raw[-1]) > 12 else None
    issued_at = None
    if isinstance(issue_raw, list) and issue_raw:
        issued_at = parse_compact(str(issue_raw[0]))
        if issued_at:
            issued_at = issued_at - timedelta(hours=8)

    return {
        "id": f"nmc:{t[0]}",
        "agency": "CMA",
        "season": season,
        "number": str(t[3] or t[4] or ""),
        "name_en": t[1] or "",
        "name_zh": t[2] or t[6] or "",
        "status": t[7] or "",
        "category": last["category"],
        "lat": last["lat"],
        "lon": last["lon"],
        "pressure_hpa": last["pressure_hpa"],
        "wind_ms": last["wind_ms"],
        "motion_dir": last.get("motion_dir"),
        "motion_speed_kmh": last.get("motion_speed_kmh"),
        "observed_at": observed,
        "updated_at": iso(issued_at) if issued_at else observed,
        "points": points,
        "forecasts": forecasts,
        "raw": t,
    }


def fetch_list() -> list[dict[str, Any]]:
    return parse_list(fetch_text(LIST_URL, timeout=30, retries=3))


def fetch_detail(storm_id: str) -> dict[str, Any] | None:
    return parse_detail(fetch_text(DETAIL_URL.format(id=storm_id), timeout=30, retries=3))


def fetch_active(max_storms: int = 8) -> list[dict[str, Any]]:
    listed = fetch_list()
    active = [x for x in listed if x.get("status") == "start"]
    # 没有 start 时，返回最近几条 stop，便于空窗期展示最近一个台风。
    if not active:
        active = listed[:1]
    out = []
    for item in active[:max_storms]:
        try:
            detail = fetch_detail(item["id"])
            if detail:
                out.append(detail)
        except Exception as exc:  # noqa: BLE001
            log.warning("NMC detail %s failed: %s", item["id"], exc)
    return out
