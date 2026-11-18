"""站点派生指标与台风 24h 加深。

所有 24h 变化都基于同一来源的观测序列，不把 METAR 的 QNH 和 SYNOP 的 MSLP
混在一条曲线里；缺测就返回 None，不用模式值补。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any

from .db import connect
from .util import iso, minutes_between, now_utc, parse_iso

log = logging.getLogger(__name__)

PRIORITY = {
    "pressure": {"SYNOP": 4, "NDBC": 3, "SHIP": 2, "WINDY": 1, "METAR": 1},
    "wind": {"METAR": 4, "SYNOP": 3, "WINDY": 2, "NDBC": 2, "SHIP": 1},
    "temp": {"METAR": 4, "SYNOP": 3, "WINDY": 2, "NDBC": 2, "SHIP": 1},
    "rh": {"SYNOP": 4, "METAR": 3, "WINDY": 2, "NDBC": 2, "SHIP": 1},
    "rain": {"SYNOP": 4, "WINDY": 2, "NDBC": 2, "METAR": 1, "SHIP": 1},
    "sea_temp": {"GDP": 4, "NDBC": 3, "SHIP": 2},
}

RANGES = {
    "pressure": (850.0, 1100.0),
    "wind": (0.0, 90.0),
    "temp": (-50.0, 55.0),
    "rh": (0.0, 100.0),
    "rain": (0.0, 500.0),
    "sea_temp": (-5.0, 40.0),
}


def _valid(variable: str, value: Any) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    lo, hi = RANGES[variable]
    if not (lo <= v <= hi):
        return None
    return v


def _obs_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    for key in ("qc_flags",):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                d[key] = []
        else:
            d[key] = []
    return d


def _choose(obs: list[dict[str, Any]], variable: str, latest: str) -> dict[str, Any] | None:
    """在最近 90 分钟内选一个变量值；时间优先，其次来源优先级。"""
    latest_dt = parse_iso(latest)
    if not latest_dt:
        return None
    candidates = []
    for o in obs:
        value = {
            "pressure": o.get("pressure_hpa"),
            "wind": o.get("wind_ms"),
            "temp": o.get("temp_c"),
            "rh": o.get("rh_pct"),
            "rain": o.get("precip_24h_mm"),
            "sea_temp": o.get("sea_temp_c"),
        }[variable]
        value = _valid(variable, value)
        if value is None:
            continue
        t = parse_iso(o.get("obs_time"))
        if not t:
            continue
        age_min = (latest_dt - t).total_seconds() / 60
        if age_min > 90:
            continue
        rank = PRIORITY[variable].get(o.get("source"), 0)
        candidates.append((t.timestamp(), rank, o))
    if not candidates:
        # 允许稍旧的数据，但标记 stale。
        for o in obs:
            value = _valid(variable, {
                "pressure": o.get("pressure_hpa"),
                "wind": o.get("wind_ms"),
                "temp": o.get("temp_c"),
                "rh": o.get("rh_pct"),
                "rain": o.get("precip_24h_mm"),
                "sea_temp": o.get("sea_temp_c"),
            }[variable])
            if value is not None:
                t = parse_iso(o.get("obs_time"))
                if t:
                    candidates.append((t.timestamp(), 0, o))
    if not candidates:
        return None
    # Windy 只有当前值，没有 24h 历史；气压/气温若有 SYNOP/METAR/NDBC，
    # 优先用有历史序列的源，避免把 24h 指标打成 null。
    if variable in ("pressure", "temp"):
        non_windy = [c for c in candidates if c[2].get("source") != "WINDY"]
        if non_windy:
            candidates = non_windy
    # 气压优先保证“同一口径的 MSLP 序列”，再比时间；风/温则优先最新。
    if variable == "pressure":
        return max(candidates, key=lambda x: (x[1], x[0]))[2]
    return max(candidates, key=lambda x: (x[0], x[1]))[2]


def _change(
    obs: list[dict[str, Any]],
    source: str,
    field: str,
    current_time: str,
    current_value: float | None,
    max_gap_min: float = 45,
) -> tuple[float | None, float | None]:
    if current_value is None:
        return None, None
    target = parse_iso(current_time) - timedelta(hours=24)
    best = None
    best_gap = None
    for o in obs:
        if o.get("source") != source or o.get(field) is None:
            continue
        t = parse_iso(o.get("obs_time"))
        if not t:
            continue
        gap = abs((t - target).total_seconds()) / 60
        if gap <= max_gap_min and (best_gap is None or gap < best_gap):
            best, best_gap = o, gap
    if best is None:
        return None, None
    delta = float(current_value) - float(best[field])
    # 物理上不太可能的 24h 变化直接丢弃，避免坏报占据排行榜。
    limit = 80.0 if field == "pressure_hpa" else 20.0
    if abs(delta) > limit:
        return None, None
    return round(delta, 1), round(best_gap or 0, 0)


def refresh_metrics() -> int:
    since = iso(now_utc() - timedelta(hours=96))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM observations WHERE obs_time >= ? ORDER BY station_id, obs_time",
            (since,),
        ).fetchall()
        stations = conn.execute("SELECT id FROM stations WHERE active=1").fetchall()
        by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_station[row["station_id"]].append(_obs_dict(row))

        count = 0
        for st in stations:
            sid = st["id"]
            obs = by_station.get(sid) or []
            if not obs:
                continue
            obs.sort(key=lambda x: x["obs_time"])
            latest = obs[-1]["obs_time"]
            p = _choose(obs, "pressure", latest)
            w = _choose(obs, "wind", latest)
            t = _choose(obs, "temp", latest)
            rh = _choose(obs, "rh", latest)
            rain = _choose(obs, "rain", latest)
            sea = _choose(obs, "sea_temp", latest)

            dp, dp_gap = _change(obs, p["source"], "pressure_hpa", p["obs_time"], p.get("pressure_hpa")) if p else (None, None)
            dt, dt_gap = _change(obs, t["source"], "temp_c", t["obs_time"], t.get("temp_c")) if t else (None, None)

            qc: list[str] = []
            latest_dt = parse_iso(latest)
            age_min = (now_utc() - latest_dt).total_seconds() / 60 if latest_dt else None
            # SYNOP 常见 1–3 小时一次，不能用 METAR 的 90 分钟阈值。
            stale_min = 240 if (p and p.get("source") == "SYNOP") else 120
            if age_min is not None and age_min > stale_min:
                qc.append("stale")
            if p and p.get("pressure_basis") == "QNH":
                qc.append("qnh")
            if dp is None:
                qc.append("no_dp24")
            if dt is None:
                qc.append("no_dt24")
            if p and dp_gap is not None and dp_gap > 20:
                qc.append("estimated")
            if age_min is not None and age_min > 90:
                qc.append("delayed")

            conn.execute(
                """
                INSERT INTO station_current
                  (station_id, obs_time, pressure_hpa, pressure_basis, pressure_source,
                   dp_24h, dp_24h_gap_min, temp_c, temp_source, dt_24h, dt_24h_gap_min,
                   rh_pct, rh_source, sea_temp_c, sea_temp_source,
                   wind_dir_deg, wind_ms, gust_ms, wind_source,
                   precip_1h_mm, precip_3h_mm, precip_24h_mm, precip_source,
                   quality, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(station_id) DO UPDATE SET
                  obs_time=excluded.obs_time,
                  pressure_hpa=excluded.pressure_hpa,
                  pressure_basis=excluded.pressure_basis,
                  pressure_source=excluded.pressure_source,
                  dp_24h=excluded.dp_24h,
                  dp_24h_gap_min=excluded.dp_24h_gap_min,
                  temp_c=excluded.temp_c,
                  temp_source=excluded.temp_source,
                  dt_24h=excluded.dt_24h,
                  dt_24h_gap_min=excluded.dt_24h_gap_min,
                  rh_pct=excluded.rh_pct,
                  rh_source=excluded.rh_source,
                  sea_temp_c=excluded.sea_temp_c,
                  sea_temp_source=excluded.sea_temp_source,
                  wind_dir_deg=excluded.wind_dir_deg,
                  wind_ms=excluded.wind_ms,
                  gust_ms=excluded.gust_ms,
                  wind_source=excluded.wind_source,
                  precip_1h_mm=excluded.precip_1h_mm,
                  precip_3h_mm=excluded.precip_3h_mm,
                  precip_24h_mm=excluded.precip_24h_mm,
                  precip_source=excluded.precip_source,
                  quality=excluded.quality,
                  updated_at=excluded.updated_at
                """,
                (
                    sid, latest,
                    p.get("pressure_hpa") if p else None,
                    p.get("pressure_basis") if p else None,
                    p.get("source") if p else None,
                    dp, dp_gap,
                    t.get("temp_c") if t else None,
                    t.get("source") if t else None,
                    dt, dt_gap,
                    rh.get("rh_pct") if rh else None,
                    rh.get("source") if rh else None,
                    sea.get("sea_temp_c") if sea else None,
                    sea.get("source") if sea else None,
                    w.get("wind_dir_deg") if w else None,
                    w.get("wind_ms") if w else None,
                    w.get("gust_ms") if w else None,
                    w.get("source") if w else None,
                    rain.get("precip_1h_mm") if rain else None,
                    rain.get("precip_3h_mm") if rain else None,
                    rain.get("precip_24h_mm") if rain else None,
                    rain.get("source") if rain else None,
                    json.dumps(sorted(set(qc)), ensure_ascii=False),
                    iso(),
                ),
            )
            count += 1
        conn.commit()
    return count


def storm_metrics(storm_id: str, points: list[dict[str, Any]]) -> dict[str, Any]:
    """根据路径点计算 24h 中心气压/风速变化（线性插值）。"""
    pts = [p for p in points if not p.get("forecast") and p.get("pressure_hpa") is not None]
    if not pts:
        return {"dp_24h": None, "dw_24h": None, "rapid": False}
    pts = sorted(pts, key=lambda p: p["valid_time"])
    last = pts[-1]
    target = parse_iso(last["valid_time"]) - timedelta(hours=24)
    before = after = None
    for p in pts:
        t = parse_iso(p["valid_time"])
        if t <= target:
            before = p
        if t >= target and after is None:
            after = p
    if before is None or after is None:
        return {"dp_24h": None, "dw_24h": None, "rapid": False}
    tb, ta = parse_iso(before["valid_time"]), parse_iso(after["valid_time"])
    if tb == ta:
        old_p, old_w = before.get("pressure_hpa"), before.get("wind_ms")
    else:
        f = (target - tb).total_seconds() / (ta - tb).total_seconds()
        old_p = before.get("pressure_hpa") + (after.get("pressure_hpa") - before.get("pressure_hpa")) * f
        if before.get("wind_ms") is None or after.get("wind_ms") is None:
            old_w = None
        else:
            old_w = before["wind_ms"] + (after["wind_ms"] - before["wind_ms"]) * f
    dp = round(last["pressure_hpa"] - old_p, 1) if old_p is not None and last.get("pressure_hpa") is not None else None
    dw = round(last["wind_ms"] - old_w, 1) if old_w is not None and last.get("wind_ms") is not None else None
    rapid = bool((dp is not None and dp <= -24) or (dw is not None and dw >= 15))
    return {"dp_24h": dp, "dw_24h": dw, "rapid": rapid}
