"""Ogimet SYNOP 陆地/岛屿站。

Ogimet 的 getsynop 按 WMO block 返回 AAXX 报文：
  https://www.ogimet.com/cgi-bin/getsynop?begin=...&end=...&block=48&lang=en

报文本身不带经纬度，因此用 NOAA ISD history 的 WMO/坐标元数据做站点匹配。
解析的组：
  1SnTTT   气温
  2SnTdTdTd 露点
  3PPPP    本站气压
  4PPPP    海平面气压
  5appp    3 小时气压倾向
  6RRR1    1/3/6 小时降水
  7RRRR    24 小时降水
  ddfff    风（节）
"""

from __future__ import annotations

import csv
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import config
from ..http import fetch_text
from ..util import fnum, iso, knot_to_ms, now_utc, parse_compact, rh_from_dewpoint

log = logging.getLogger(__name__)

BASE = "https://www.ogimet.com/cgi-bin/getsynop"
META_CACHE = config.DATA_DIR / "wmo_stations.json"
META_CSV = config.ISD_HISTORY


def load_station_meta(force: bool = False) -> dict[str, dict[str, Any]]:
    """从 ISD history 构建 WMO 5 位码 -> 站点元数据。"""
    if (
        not force
        and META_CACHE.exists()
        and (not META_CSV.exists() or META_CACHE.stat().st_mtime >= META_CSV.stat().st_mtime)
    ):
        try:
            rows = json.loads(META_CACHE.read_text(encoding="utf-8"))
            return {str(r["wmo"]): r for r in rows}
        except Exception:
            pass

    rows: dict[str, dict[str, Any]] = {}
    if not META_CSV.exists():
        log.warning("ISD history 不存在：%s；SYNOP 将只有报文没有坐标", META_CSV)
        return {}
    with META_CSV.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                lat = float(row["LAT"])
                lon = float(row["LON"])
                end = int(row["END"] or 0)
                usaf = (row.get("USAF") or "").strip()
                if not (5 <= lat <= 40 and 100 <= lon <= 160):
                    continue
                if end < 20200101:
                    continue
                wmo = usaf[:5]
                if not wmo.isdigit():
                    continue
                rec = {
                    "wmo": wmo,
                    "usaf": usaf,
                    "name": (row.get("STATION NAME") or "").strip(),
                    "country": (row.get("CTRY") or "").strip(),
                    "icao": (row.get("ICAO") or "").strip(),
                    "lat": lat,
                    "lon": lon,
                    "elev_m": fnum(row.get("ELEV(M)")),
                    "end": end,
                }
                prev = rows.get(wmo)
                if prev is None or rec["end"] > prev["end"]:
                    rows[wmo] = rec
            except Exception:
                continue
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    META_CACHE.write_text(json.dumps(list(rows.values()), ensure_ascii=False), encoding="utf-8")
    return rows


def _wind(token: str) -> tuple[float | None, float | None]:
    """SYNOP Nddff：N 云量，dd 风向，ff 风速（节）。"""
    if not token or len(token) != 5:
        return None, None
    body = token[1:]
    if not body.isdigit():
        return None, None
    dd = int(body[:2])
    ff = int(body[2:])
    if dd > 36:
        return None, None
    return (dd * 10.0) % 360 if dd != 36 else None, knot_to_ms(ff)


def _temp(token: str) -> float | None:
    if len(token) != 5 or not token.isdigit() or not token.startswith(("1", "2")):
        return None
    sign = -1 if token[1] == "1" else 1
    value = sign * int(token[2:]) / 10.0
    # 10903 这类是风组 Nddff，不是 +90.3°C；用物理范围排除。
    if not -60 <= value <= 60:
        return None
    return value


def _pressure(token: str, base: str) -> float | None:
    if len(token) != 5 or not token.startswith(base) or not token[1:].isdigit():
        return None
    p = int(token[1:]) / 10.0
    if p < 500:
        p += 1000.0
    return round(p, 1)


def _tendency(token: str) -> tuple[float | None, str | None]:
    if len(token) != 5 or not token.startswith("5") or not token[1:].isdigit():
        return None, None
    characteristic = int(token[1])
    amount = int(token[2:]) / 10.0
    if characteristic in (0, 1, 2, 3):
        return amount, "rising"
    if characteristic == 4:
        return 0.0, "steady"
    if characteristic in (5, 6, 7, 8):
        return -amount, "falling"
    return None, None


def _precip(token: str, kind: str) -> float | None:
    if kind == "6":
        # 6RRR1/2/3/4/5：RRR 为降水量，单位 0.1 mm
        if len(token) != 5 or not token.startswith("6") or not token[1:4].isdigit():
            return None
        value = int(token[1:4]) / 10.0
        return None if value >= 99.9 else value
    if kind == "7":
        # 7RRRR：24 小时降水量，单位 0.1 mm；9999 是缺测/特殊值。
        if len(token) != 5 or not token.startswith("7") or not token[1:].isdigit():
            return None
        value = int(token[1:]) / 10.0
        return None if value >= 999.0 else value
    return None


def parse_report(line: str, meta: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    parts = line.split(",")
    if len(parts) < 7:
        return None
    station_id = parts[0].strip()
    if not station_id.isdigit():
        return None
    wmo = station_id[:5]
    info = meta.get(wmo)
    if not info:
        return None
    try:
        dt = datetime(
            int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), tzinfo=now_utc().tzinfo
        )
    except Exception:
        return None
    body = ",".join(parts[6:]).strip()
    if "NIL" in body.upper():
        return None
    tokens = body.replace("=", " ").split()
    if not tokens:
        return None
    # 去掉 AAXX/BBXX、时间组、站号
    if tokens and tokens[0] in ("AAXX", "BBXX", "AAXX=", "BBXX="):
        tokens = tokens[1:]
    if len(tokens) >= 2:
        tokens = tokens[2:]

    # 333 之后是附加组，不能覆盖主组（例如 59002 是 333 内的气压倾向补充）。
    main_tokens = tokens[: tokens.index("333")] if "333" in tokens else tokens

    # 温度组作为锚点；它前面的 5 位组是风。10903 这类风组会被 _temp 的范围检查排除。
    temp_idx = next(
        (
            i
            for i, t in enumerate(main_tokens)
            if re.fullmatch(r"1[01]\d{3}", t) and _temp(t) is not None
        ),
        None,
    )
    temp = _temp(main_tokens[temp_idx]) if temp_idx is not None else None
    wind_dir = wind_ms = None
    if temp_idx is not None and temp_idx > 0:
        wind_dir, wind_ms = _wind(main_tokens[temp_idx - 1])
    dew_idx = next(
        (
            i
            for i, t in enumerate(main_tokens)
            if re.fullmatch(r"2[01]\d{3}", t) and _temp(t) is not None
        ),
        None,
    )
    dew = _temp(main_tokens[dew_idx]) if dew_idx is not None else None

    station_pressure = None
    slp = None
    tendency = None
    tendency_dir = None
    precip_3h = None
    precip_24h = None
    for token in main_tokens:
        if re.fullmatch(r"3\d{4}", token):
            station_pressure = _pressure(token, "3")
        elif re.fullmatch(r"4\d{4}", token):
            slp = _pressure(token, "4")
        elif re.fullmatch(r"5\d{4}", token):
            tendency, tendency_dir = _tendency(token)
        elif re.fullmatch(r"6\d{4}", token):
            precip_3h = _precip(token, "6")
        elif re.fullmatch(r"7\d{4}", token):
            precip_24h = _precip(token, "7")

    # 333 之后是附加组。24 小时降水 7RRRR 通常在 333 段。
    section3 = tokens[tokens.index("333") + 1 :] if "333" in tokens else []
    for token in section3:
        if re.fullmatch(r"7\d{4}", token):
            precip_24h = _precip(token, "7")
            break
        if re.fullmatch(r"6\d{4}", token) and precip_3h is None:
            precip_3h = _precip(token, "6")

    pressure = slp or station_pressure
    basis = "MSLP" if slp is not None else ("STATION" if station_pressure is not None else None)

    qc = []
    if pressure is not None and not 850 <= pressure <= 1100:
        qc.append("pressure_range")
    if temp is not None and not -50 <= temp <= 55:
        qc.append("temp_range")
    if wind_ms is not None and not 0 <= wind_ms <= 120:
        qc.append("wind_range")
    if pressure is None and temp is None and wind_ms is None:
        return None

    return {
        "station": {
            "id": f"synop:{wmo}",
            "wmo_id": wmo,
            "icao_id": info.get("icao") or None,
            "name": info.get("name") or wmo,
            "country": info.get("country") or "",
            "lat": info["lat"],
            "lon": info["lon"],
            "elev_m": info.get("elev_m"),
            "type": "land",
            "source": "SYNOP",
        },
        "obs": {
            "obs_time": iso(dt),
            "source": "SYNOP",
            "wind_dir_deg": wind_dir,
            "wind_ms": round(wind_ms, 1) if wind_ms is not None else None,
            "gust_ms": None,
            "pressure_hpa": pressure,
            "pressure_basis": basis,
            "temp_c": temp,
            "dewpoint_c": dew,
            "rh_pct": rh_from_dewpoint(temp, dew),
            "precip_1h_mm": None,
            "precip_3h_mm": precip_3h,
            "precip_24h_mm": precip_24h,
            "visibility_m": None,
            "weather": "",
            "clouds": "",
            "qc_flags": qc,
            "raw_json": {"line": line, "tendency_hpa_3h": tendency, "tendency_dir": tendency_dir},
        },
    }


def fetch_block(block: int, begin: datetime, end: datetime) -> list[str]:
    params = {
        "begin": begin.strftime("%Y%m%d%H%M"),
        "end": end.strftime("%Y%m%d%H%M"),
        "block": str(block),
        "lang": "en",
    }
    # 直接用 requests 的参数拼接，避免手工转义。
    from ..http import fetch

    resp = fetch(BASE, params=params, timeout=45, retries=2)
    resp.encoding = "latin-1"
    return [ln.strip() for ln in resp.text.splitlines() if ln.strip()]


def fetch(hours: int | None = None) -> list[dict[str, Any]]:
    meta = load_station_meta()
    if not meta:
        return []
    hours = hours or config.SYNOP_LOOKBACK_HOURS
    end = now_utc()
    begin = end - timedelta(hours=hours)
    out: list[dict[str, Any]] = []
    for block in config.SYNOP_BLOCKS:
        try:
            lines = fetch_block(block, begin, end)
            for line in lines:
                parsed = parse_report(line, meta)
                if parsed:
                    out.append(parsed)
        except Exception as exc:  # noqa: BLE001
            log.warning("SYNOP block %s failed: %s", block, exc)
        time.sleep(0.15)
    # 去重：同一站同一时刻保留最后一条
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in out:
        unique[(item["station"]["id"], item["obs"]["obs_time"])] = item
    return list(unique.values())
