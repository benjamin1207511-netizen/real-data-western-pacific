"""SAMOS 科考船/商船近实时观测。

数据来自 COAPS SAMOS 的 THREDDS Preliminary Ship Data：
  https://tds.coaps.fsu.edu/thredds/catalog/samos/data/quick/catalog.html
  https://samos.coaps.fsu.edu/html/Preliminary_ifile_json.php

SAMOS 的 quick 文件按船/天存放，netCDF 变量包括：
  lat, lon, time, DIR, SPD, P, T, RH
只保留落在 5–40N / 100–160E 的船舶。若当前没有船在该区域，返回空列表，
不会用模式值补位。
"""

from __future__ import annotations

import io
import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import netCDF4

from .. import config
from ..util import fnum, iso, rh_from_dewpoint

log = logging.getLogger(__name__)

LIST_URL = "https://samos.coaps.fsu.edu/html/Preliminary_ifile_json.php"
FILE_URL = (
    "https://tds.coaps.fsu.edu/thredds/fileServer/samos/data/quick/"
    "{code}/{year}/{code}_{date}v{ver}.nc"
)
CACHE = config.CACHE_DIR / "samos"


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _read_file(code: str, date: str, ver: str) -> dict[str, Any] | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE / f"{code}_{date}_{ver}.nc"
    if cache_path.exists() and cache_path.stat().st_size > 2000:
        raw = cache_path.read_bytes()
    else:
        raw = _get(FILE_URL.format(code=code, year=date[:4], date=date, ver=ver))
        cache_path.write_bytes(raw)
    ds = netCDF4.Dataset(f"{code}.nc", memory=raw)
    try:
        lat = float(ds.variables["lat"][-1])
        lon = float(ds.variables["lon"][-1])
        if not (config.DOMAIN[1] <= lat <= config.DOMAIN[3] and config.DOMAIN[0] <= lon <= config.DOMAIN[2]):
            return None
        # time: minutes since 1980-01-01; date/time_of_day 也可用。
        tvar = ds.variables["time"]
        dt = datetime(1980, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=float(tvar[-1]))
        date_val = int(ds.variables["date"][-1]) if "date" in ds.variables else None
        tod = int(ds.variables["time_of_day"][-1]) if "time_of_day" in ds.variables else None
        if date_val and tod is not None and 0 <= tod < 240000:
            try:
                dt = datetime.strptime(f"{date_val:08d}{tod:06d}", "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except Exception:
                pass
        speed = fnum(ds.variables["SPD"][-1]) if "SPD" in ds.variables else None
        direction = fnum(ds.variables["DIR"][-1]) if "DIR" in ds.variables else None
        pressure = fnum(ds.variables["P"][-1]) if "P" in ds.variables else None
        temp = fnum(ds.variables["T"][-1]) if "T" in ds.variables else None
        rh = fnum(ds.variables["RH"][-1]) if "RH" in ds.variables else None
        if speed is None and pressure is None and temp is None:
            return None
        name = getattr(ds, "title", None) or code
        return {
            "station": {
                "id": f"samos:{code}",
                "wmo_id": None,
                "icao_id": None,
                "name": str(name),
                "country": "SHIP",
                "lat": lat,
                "lon": lon,
                "elev_m": 0,
                "type": "ship",
                "source": "SHIP",
            },
            "obs": {
                "obs_time": iso(dt),
                "source": "SHIP",
                "wind_dir_deg": direction,
                "wind_ms": round(speed, 1) if speed is not None else None,
                "gust_ms": None,
                "pressure_hpa": pressure,
                "pressure_basis": "MSLP",
                "temp_c": temp,
                "dewpoint_c": None,
                "rh_pct": rh if rh is not None else rh_from_dewpoint(temp, None),
                "precip_1h_mm": None,
                "precip_3h_mm": None,
                "precip_24h_mm": None,
                "visibility_m": None,
                "weather": "",
                "clouds": "",
                "qc_flags": [],
                "raw_json": {"code": code, "date": date, "ver": ver, "file": str(cache_path)},
            },
        }
    finally:
        ds.close()


def _one(code: str, info: dict[str, Any]) -> dict[str, Any] | None:
    date = str(info.get("last_date") or "")
    if len(date) != 8 or not date.isdigit():
        return None
    for ver in ("10001", "10002", "10003", "10004"):
        try:
            return _read_file(code, date, ver)
        except Exception:
            continue
    return None


def fetch() -> list[dict[str, Any]]:
    try:
        data = json.loads(_get(LIST_URL, timeout=25).decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        log.debug("SAMOS list failed: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    active = {
        k: v
        for k, v in data.items()
        if isinstance(v, dict)
        and v.get("last_date")
        and (v.get("ndays_last_received") is None or int(v.get("ndays_last_received") or 0) <= 7)
    }
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_one, code, info) for code, info in active.items()]
        for f in as_completed(futures):
            try:
                item = f.result()
                if item:
                    out.append(item)
            except Exception:
                continue
    return out
