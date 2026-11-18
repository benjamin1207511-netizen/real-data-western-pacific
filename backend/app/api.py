"""REST API。"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from . import analytics, config, ingest
from .sources import satellite
from .db import connect
from .util import bearing_deg, compass, haversine_km, iso, now_utc, parse_iso

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _rows(rows) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _storm(row: Any) -> dict[str, Any]:
    d = dict(row)
    with connect() as conn:
        points = conn.execute(
            "SELECT * FROM track_points WHERE storm_id=? ORDER BY valid_time",
            (d["id"],),
        ).fetchall()
    pts = []
    for p in points:
        item = dict(p)
        item["wind_radii"] = _decode_json(item.pop("wind_radii_json", None), {})
        item["forecast"] = bool(item.get("forecast"))
        pts.append(item)
    d["points"] = pts
    d["metrics"] = analytics.storm_metrics(d["id"], pts)
    d.pop("raw_json", None)
    return d


def _station(row: Any, storm: dict[str, Any] | None = None) -> dict[str, Any]:
    d = dict(row)
    d["quality"] = _decode_json(d.get("quality"), [])
    d["wind_dir_text"] = compass(d.get("wind_dir_deg"))
    d["age_min"] = None
    t = parse_iso(d.get("obs_time"))
    if t:
        d["age_min"] = round((now_utc() - t).total_seconds() / 60)
    if storm and d.get("lat") is not None and d.get("lon") is not None:
        try:
            d["distance_km"] = round(
                haversine_km(storm["lat"], storm["lon"], d["lat"], d["lon"]), 0
            )
            d["bearing_deg"] = round(bearing_deg(storm["lat"], storm["lon"], d["lat"], d["lon"]), 0)
        except Exception:
            d["distance_km"] = None
            d["bearing_deg"] = None
    return d


@router.get("/config")
def get_config() -> dict[str, Any]:
    return {
        "domain": {
            "west": config.DOMAIN[0],
            "south": config.DOMAIN[1],
            "east": config.DOMAIN[2],
            "north": config.DOMAIN[3],
        },
        "name": "风眼 TC WATCH",
        "default_channel": satellite.default_channel(),
        "channels": {k: {"label": v.label} for k, v in config.SATELLITE_CHANNELS.items()},
    }


@router.get("/overview")
def overview() -> dict[str, Any]:
    with connect() as conn:
        storms = conn.execute(
            "SELECT * FROM storms WHERE status='start' ORDER BY observed_at DESC LIMIT 8"
        ).fetchall()
        if not storms:
            storms = conn.execute(
                "SELECT * FROM storms ORDER BY observed_at DESC LIMIT 1"
            ).fetchall()
        storm_data = [_storm(s) for s in storms]
        primary = storm_data[0] if storm_data else None

        station_count = conn.execute("SELECT COUNT(*) c FROM station_current").fetchone()["c"]
        total_stations = conn.execute("SELECT COUNT(*) c FROM stations WHERE active=1").fetchone()["c"]
        last_obs = conn.execute("SELECT MAX(obs_time) t FROM station_current").fetchone()["t"]
        by_type = conn.execute(
            "SELECT type, COUNT(*) c FROM stations WHERE active=1 GROUP BY type"
        ).fetchall()
        statuses = conn.execute("SELECT * FROM source_status ORDER BY source").fetchall()
        imerg = conn.execute("SELECT * FROM imerg_meta WHERE id=1").fetchone()
        sat = satellite.latest_frame(satellite.default_channel())

    return {
        "generated_at": iso(),
        "storms": storm_data,
        "primary_storm": primary,
        "station_count": station_count,
        "total_stations": total_stations,
        "last_obs": last_obs,
        "by_type": {r["type"]: r["c"] for r in by_type},
        "sources": _rows(statuses),
        "default_channel": satellite.default_channel(),
        "satellite_latest": sat,
        "imerg": dict(imerg) if imerg else None,
    }


@router.get("/storms")
def storms() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM storms ORDER BY observed_at DESC LIMIT 30").fetchall()
    return [_storm(r) for r in rows]


@router.get("/storms/{storm_id}")
def storm_detail(storm_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM storms WHERE id=?", (storm_id,)).fetchone()
    if not row:
        raise HTTPException(404, "storm not found")
    return _storm(row)


@router.get("/stations")
def stations(
    bbox: str | None = Query(None, description="west,south,east,north"),
    storm: str | None = None,
    limit: int = Query(1200, ge=1, le=3000),
    types: str | None = None,
) -> list[dict[str, Any]]:
    west, south, east, north = config.DOMAIN
    if bbox:
        try:
            west, south, east, north = [float(x) for x in bbox.split(",")]
        except Exception:
            raise HTTPException(422, "bbox must be west,south,east,north")
    type_list = [x.strip() for x in (types or "").split(",") if x.strip()]
    sql = """
        SELECT s.id, s.wmo_id, s.icao_id, s.name, s.country, s.lat, s.lon, s.elev_m, s.type,
               s.primary_source, c.obs_time, c.pressure_hpa, c.pressure_basis, c.pressure_source,
               c.dp_24h, c.dp_24h_gap_min, c.temp_c, c.temp_source, c.dt_24h, c.dt_24h_gap_min,
               c.rh_pct, c.sea_temp_c, c.sea_temp_source,
               c.wind_dir_deg, c.wind_ms, c.gust_ms, c.wind_source,
               c.precip_1h_mm, c.precip_3h_mm, c.precip_24h_mm, c.precip_source,
               c.quality, c.updated_at
        FROM stations s
        JOIN station_current c ON c.station_id = s.id
        WHERE s.active=1 AND s.lat BETWEEN ? AND ? AND s.lon BETWEEN ? AND ?
    """
    params: list[Any] = [south, north, west, east]
    if type_list:
        sql += f" AND s.type IN ({','.join('?' for _ in type_list)})"
        params.extend(type_list)
    sql += " ORDER BY c.obs_time DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    storm_row = None
    if storm:
        with connect() as conn:
            sr = conn.execute("SELECT * FROM storms WHERE id=?", (storm,)).fetchone()
            if sr:
                storm_row = dict(sr)
    data = [_station(r, storm_row) for r in rows]
    if storm_row:
        data.sort(key=lambda x: x.get("distance_km") or 99999)
    return data


@router.get("/stations/{station_id}")
def station_detail(station_id: str, hours: int = Query(72, ge=6, le=168)) -> dict[str, Any]:
    since = iso(now_utc() - timedelta(hours=hours))
    with connect() as conn:
        st = conn.execute(
            """
            SELECT s.*, c.* FROM stations s
            LEFT JOIN station_current c ON c.station_id=s.id
            WHERE s.id=?
            """,
            (station_id,),
        ).fetchone()
        if not st:
            raise HTTPException(404, "station not found")
        obs = conn.execute(
            """
            SELECT * FROM observations
            WHERE station_id=? AND obs_time>=?
            ORDER BY obs_time
            """,
            (station_id, since),
        ).fetchall()
    current = _station(st)
    base = dict(st)
    # 避免 s.* 和 c.* 同名列互相覆盖后失去 station 字段。
    for key in ("id", "name", "lat", "lon", "type", "country", "wmo_id", "icao_id", "elev_m", "primary_source"):
        if key in base:
            current[key] = base[key]

    obs_list = []
    for r in obs:
        d = dict(r)
        d["qc_flags"] = _decode_json(d.get("qc_flags"), [])
        d["raw_json"] = None
        obs_list.append(d)

    pressure_source = current.get("pressure_source")
    temp_source = current.get("temp_source")
    wind_source = current.get("wind_source")
    rh_source = current.get("rh_source")
    sea_source = current.get("sea_temp_source")
    precip_source = current.get("precip_source")

    def series(source: str | None, field: str, scale: float = 1.0) -> list[list[float]]:
        if not source:
            return []
        out = []
        for o in obs_list:
            if o.get("source") != source:
                continue
            v = o.get(field)
            if v is None:
                continue
            out.append([o["obs_time"], round(float(v) * scale, 2)])
        return out

    return {
        "station": current,
        "series": {
            "pressure": series(pressure_source, "pressure_hpa"),
            "temp": series(temp_source, "temp_c"),
            "rh": series(rh_source, "rh_pct"),
            "sea_temp": series(sea_source, "sea_temp_c"),
            "wind": series(wind_source, "wind_ms"),
            "gust": series(wind_source, "gust_ms"),
            "rain1h": series(precip_source, "precip_1h_mm"),
            "rain3h": series(precip_source, "precip_3h_mm"),
            "rain24h": series(precip_source, "precip_24h_mm"),
        },
        "sources": {
            "pressure": pressure_source,
            "temp": temp_source,
            "wind": wind_source,
            "rh": rh_source,
            "sea_temp": sea_source,
            "precip": precip_source,
        },
        "observations": obs_list[-400:],
    }


@router.get("/rankings")
def rankings(
    metric: str = Query("dp24", pattern="^(dp24|dt24|wind|rain|gust)$"),
    limit: int = Query(12, ge=1, le=100),
    storm: str | None = None,
) -> list[dict[str, Any]]:
    column = {
        "dp24": "c.dp_24h ASC",
        "dt24": "c.dt_24h ASC",
        "wind": "c.wind_ms DESC",
        "gust": "c.gust_ms DESC",
        "rain": "c.precip_24h_mm DESC",
    }[metric]
    where = {
        "dp24": "c.dp_24h IS NOT NULL",
        "dt24": "c.dt_24h IS NOT NULL",
        "wind": "c.wind_ms IS NOT NULL",
        "gust": "c.gust_ms IS NOT NULL",
        "rain": "c.precip_24h_mm IS NOT NULL AND c.precip_24h_mm > 0",
    }[metric]
    sql = f"""
        SELECT s.id, s.name, s.country, s.lat, s.lon, s.type,
               c.obs_time, c.dp_24h, c.dt_24h, c.wind_ms, c.gust_ms, c.precip_24h_mm,
               c.pressure_hpa, c.pressure_source, c.wind_source, c.temp_source, c.precip_source,
               c.quality, c.age_min
        FROM stations s JOIN (
          SELECT *, CAST((julianday('now') - julianday(obs_time)) * 1440 AS INTEGER) AS age_min
          FROM station_current
        ) c ON c.station_id = s.id
        WHERE s.active=1 AND {where}
        ORDER BY {column}
        LIMIT ?
    """
    with connect() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    storm_row = None
    if storm:
        with connect() as conn:
            sr = conn.execute("SELECT * FROM storms WHERE id=?", (storm,)).fetchone()
            if sr:
                storm_row = dict(sr)
    out = []
    for r in rows:
        d = _station(r, storm_row)
        out.append(d)
    return out


@router.get("/satellite/frames")
def satellite_frames(
    channel: str | None = None,
    hours: int = Query(3, ge=1, le=6),
) -> dict[str, Any]:
    channel = channel or satellite.default_channel()
    if channel not in config.SATELLITE_CHANNELS:
        raise HTTPException(422, "channel must be visible or ir")
    frames = satellite.list_frames(channel, hours=hours)
    return {
        "channel": channel,
        "label": config.SATELLITE_CHANNELS[channel].label,
        "frames": frames,
        "default_channel": satellite.default_channel(),
    }


@router.get("/imerg")
def imerg() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM imerg_meta WHERE id=1").fetchone()
    return {
        "frame_time": row["frame_time"] if row else None,
        "updated_at": row["updated_at"] if row else None,
        "tile_template": (
            "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
            f"{config.IMERG_LAYER}/default/{{time}}/{config.IMERG_MATRIX}/{{z}}/{{y}}/{{x}}.png"
        ),
    }


@router.get("/status")
def status() -> dict[str, Any]:
    with connect() as conn:
        sources = conn.execute("SELECT * FROM source_status ORDER BY source").fetchall()
        counts = {
            "stations": conn.execute("SELECT COUNT(*) c FROM stations WHERE active=1").fetchone()["c"],
            "current": conn.execute("SELECT COUNT(*) c FROM station_current").fetchone()["c"],
            "observations": conn.execute(
                "SELECT COUNT(*) c FROM observations WHERE obs_time>=?",
                (iso(now_utc() - timedelta(hours=72)),),
            ).fetchone()["c"],
            "storms": conn.execute("SELECT COUNT(*) c FROM storms WHERE status='start'").fetchone()["c"],
            "tracks": conn.execute("SELECT COUNT(*) c FROM track_points").fetchone()["c"],
            "frames": conn.execute("SELECT COUNT(*) c FROM satellite_frames").fetchone()["c"],
        }
        latest = {
            "observation": conn.execute("SELECT MAX(obs_time) t FROM observations").fetchone()["t"],
            "frame": conn.execute("SELECT MAX(frame_time) t FROM satellite_frames").fetchone()["t"],
        }
        by_type = conn.execute(
            "SELECT type, COUNT(*) c FROM stations WHERE active=1 GROUP BY type"
        ).fetchall()
    return {
        "generated_at": iso(),
        "sources": _rows(sources),
        "counts": counts,
        "latest": latest,
        "by_type": {r["type"]: r["c"] for r in by_type},
    }


@router.post("/refresh")
def refresh() -> dict[str, Any]:
    import threading

    threading.Thread(target=ingest.refresh_all, name="tcw-manual-refresh", daemon=True).start()
    return {"ok": True, "message": "refresh started"}


@router.get("/export/stations.csv")
def export_stations() -> Response:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.wmo_id, s.icao_id, s.name, s.country, s.type, s.lat, s.lon, s.elev_m,
                   c.obs_time, c.pressure_hpa, c.pressure_basis, c.pressure_source,
                   c.dp_24h, c.dp_24h_gap_min, c.temp_c, c.temp_source, c.dt_24h,
                   c.rh_pct, c.sea_temp_c, c.sea_temp_source,
                   c.wind_dir_deg, c.wind_ms, c.gust_ms, c.wind_source,
                   c.precip_1h_mm, c.precip_3h_mm, c.precip_24h_mm, c.precip_source,
                   c.quality
            FROM stations s JOIN station_current c ON c.station_id=s.id
            WHERE s.active=1
            ORDER BY s.name
            """
        ).fetchall()
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow([
        "station_id", "wmo", "icao", "name", "country", "type", "lat", "lon", "elev_m",
        "obs_time_utc", "pressure_hpa", "pressure_basis", "pressure_source",
        "dp_24h_hpa", "dp_24h_gap_min", "temp_c", "temp_source", "dt_24h_c",
        "rh_pct", "sea_temp_c", "sea_temp_source", "wind_dir_deg", "wind_ms", "gust_ms", "wind_source",
        "precip_1h_mm", "precip_3h_mm", "precip_24h_mm", "precip_source", "quality",
    ])
    for r in rows:
        d = dict(r)
        writer.writerow([
            d.get("id"), d.get("wmo_id"), d.get("icao_id"), d.get("name"), d.get("country"),
            d.get("type"), d.get("lat"), d.get("lon"), d.get("elev_m"), d.get("obs_time"),
            d.get("pressure_hpa"), d.get("pressure_basis"), d.get("pressure_source"),
            d.get("dp_24h"), d.get("dp_24h_gap_min"), d.get("temp_c"), d.get("temp_source"),
            d.get("dt_24h"), d.get("rh_pct"), d.get("sea_temp_c"), d.get("sea_temp_source"),
            d.get("wind_dir_deg"), d.get("wind_ms"),
            d.get("gust_ms"), d.get("wind_source"), d.get("precip_1h_mm"),
            d.get("precip_3h_mm"), d.get("precip_24h_mm"), d.get("precip_source"),
            d.get("quality"),
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=tc-watch-stations.csv"},
    )


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "time": iso()}
