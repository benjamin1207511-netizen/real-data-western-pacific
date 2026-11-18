"""数据抓取编排：入库、状态、后台调度、失败重试。"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Callable

from . import analytics, config
from .db import connect, init_db
from .sources import gdp, jma, metar, ndbc, nmc, samos, satellite, ship, synop, windy
from .util import iso, json_dumps, now_utc, parse_iso

log = logging.getLogger(__name__)

LABELS = {
    "nmc": "CMA 台风",
    "jma": "JMA 台风",
    "metar": "METAR 机场站",
    "synop": "Ogimet SYNOP",
    "ndbc": "NDBC 浮标",
    "ship": "SAMOS 船舶",
    "windy": "Windy 站点",
    "gdp": "GDP 漂流浮标",
    "satellite": "Himawari-9 云图",
    "imerg": "IMERG 降水",
    "metrics": "派生指标",
}


def set_status(
    source: str,
    status: str,
    *,
    count: int | None = None,
    latency_ms: int | None = None,
    message: str | None = None,
) -> None:
    now = iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO source_status(source,label,status,last_attempt,last_success,latency_ms,item_count,message)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(source) DO UPDATE SET
              label=excluded.label,
              status=excluded.status,
              last_attempt=excluded.last_attempt,
              last_success=CASE WHEN excluded.status='ok' THEN excluded.last_attempt ELSE source_status.last_success END,
              latency_ms=excluded.latency_ms,
              item_count=excluded.item_count,
              message=excluded.message
            """,
            (source, LABELS.get(source, source), status, now, now if status == "ok" else None, latency_ms, count, message),
        )


def run_job(source: str, fn: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    set_status(source, "running")
    try:
        result = fn()
        count = result if isinstance(result, int) else None
        set_status(
            source,
            "ok",
            count=count,
            latency_ms=int((time.perf_counter() - started) * 1000),
            message=None,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("%s failed", source)
        set_status(
            source,
            "error",
            latency_ms=int((time.perf_counter() - started) * 1000),
            message=str(exc)[:500],
        )
        return None


def _ensure_station(conn, st: dict[str, Any]) -> str:
    source = st.get("source") or "UNKNOWN"
    source_id = str(st["id"])
    row = conn.execute(
        "SELECT station_id FROM station_aliases WHERE source=? AND source_id=?",
        (source, source_id),
    ).fetchone()
    if row:
        return row["station_id"]

    station_id = source_id
    found = None
    for col, key in (("icao_id", "icao_id"), ("wmo_id", "wmo_id")):
        value = st.get(key)
        if value:
            found = conn.execute(
                f"SELECT id FROM stations WHERE {col}=? LIMIT 1", (str(value),)
            ).fetchone()
            if found:
                station_id = found["id"]
                break
    if not found:
        # 没有代码的站，按 2km 内同名/近名合并；否则独立成站。
        name = (st.get("name") or "").lower()
        if name:
            near = conn.execute(
                "SELECT id, name, lat, lon FROM stations WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (
                    st["lat"] - 0.02, st["lat"] + 0.02,
                    st["lon"] - 0.02, st["lon"] + 0.02,
                ),
            ).fetchall()
            for cand in near:
                cname = (cand["name"] or "").lower()
                if name[:6] and (name[:6] in cname or cname[:6] in name):
                    station_id = cand["id"]
                    found = cand
                    break

    if found:
        conn.execute(
            "UPDATE stations SET icao_id=COALESCE(icao_id,?), wmo_id=COALESCE(wmo_id,?), updated_at=? WHERE id=?",
            (st.get("icao_id"), st.get("wmo_id"), iso(), station_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO stations(id,wmo_id,icao_id,name,name_zh,country,lat,lon,elev_m,type,primary_source,active,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)
            ON CONFLICT(id) DO UPDATE SET
              wmo_id=COALESCE(stations.wmo_id,excluded.wmo_id),
              icao_id=COALESCE(stations.icao_id,excluded.icao_id),
              name=excluded.name, lat=excluded.lat, lon=excluded.lon,
              elev_m=COALESCE(excluded.elev_m,stations.elev_m),
              type=excluded.type, primary_source=excluded.primary_source, updated_at=excluded.updated_at
            """,
            (
                station_id, st.get("wmo_id"), st.get("icao_id"), st.get("name") or station_id,
                st.get("name_zh"), st.get("country") or "", st["lat"], st["lon"], st.get("elev_m"),
                st.get("type") or "land", source, iso(),
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO station_aliases(source,source_id,station_id) VALUES (?,?,?)",
        (source, source_id, station_id),
    )
    return station_id


def _insert_obs(conn, station_id: str, obs: dict[str, Any]) -> None:
    raw = obs.get("raw_json")
    if not isinstance(raw, str):
        raw = json_dumps(raw)
    qc = obs.get("qc_flags") or []
    if not isinstance(qc, str):
        qc = json_dumps(qc)
    conn.execute(
        """
        INSERT INTO observations
          (station_id, obs_time, source, wind_dir_deg, wind_ms, gust_ms,
           pressure_hpa, pressure_basis, temp_c, dewpoint_c, rh_pct,
           sea_temp_c, precip_1h_mm, precip_3h_mm, precip_24h_mm, visibility_m, weather, clouds,
           qc_flags, raw_json, ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(station_id,obs_time,source) DO UPDATE SET
          wind_dir_deg=excluded.wind_dir_deg, wind_ms=excluded.wind_ms, gust_ms=excluded.gust_ms,
          pressure_hpa=excluded.pressure_hpa, pressure_basis=excluded.pressure_basis,
          temp_c=excluded.temp_c, dewpoint_c=excluded.dewpoint_c, rh_pct=excluded.rh_pct,
          sea_temp_c=excluded.sea_temp_c,
          precip_1h_mm=excluded.precip_1h_mm, precip_3h_mm=excluded.precip_3h_mm,
          precip_24h_mm=excluded.precip_24h_mm, visibility_m=excluded.visibility_m,
          weather=excluded.weather, clouds=excluded.clouds, qc_flags=excluded.qc_flags,
          raw_json=excluded.raw_json, ingested_at=excluded.ingested_at
        """,
        (
            station_id, obs["obs_time"], obs.get("source") or "UNKNOWN",
            obs.get("wind_dir_deg"), obs.get("wind_ms"), obs.get("gust_ms"),
            obs.get("pressure_hpa"), obs.get("pressure_basis"),
            obs.get("temp_c"), obs.get("dewpoint_c"), obs.get("rh_pct"),
            obs.get("sea_temp_c"),
            obs.get("precip_1h_mm"), obs.get("precip_3h_mm"), obs.get("precip_24h_mm"),
            obs.get("visibility_m"), obs.get("weather") or "", obs.get("clouds") or "",
            qc, raw, iso(),
        ),
    )


def _save_batch(items: list[dict[str, Any]]) -> int:
    if not items:
        return 0
    with connect() as conn:
        count = 0
        for item in items:
            try:
                sid = _ensure_station(conn, item["station"])
                _insert_obs(conn, sid, item["obs"])
                count += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("insert observation failed: %s", exc)
        conn.commit()
    analytics.refresh_metrics()
    return count


def refresh_metar() -> int:
    return _save_batch(metar.fetch())


def refresh_synop(hours: int | None = None) -> int:
    return _save_batch(synop.fetch(hours))


def refresh_synop_backfill() -> int:
    """首次启动或跨天重启时，先回填 30h，保证 24h 变压/变温可用。"""
    with connect() as conn:
        dp_count = conn.execute(
            "SELECT COUNT(*) c FROM station_current WHERE dp_24h IS NOT NULL"
        ).fetchone()["c"]
    if dp_count < 100:
        log.info("SYNOP backfill 30h (current dp_count=%s)", dp_count)
        return refresh_synop(30)
    return refresh_synop()


def refresh_ndbc() -> int:
    return _save_batch(ndbc.fetch())


def refresh_ship() -> int:
    # NMC 的 shipStation 经常为空；SAMOS 科考船/商船是更稳定的补充。
    return _save_batch(ship.fetch() + samos.fetch())


def refresh_windy() -> int:
    return _save_batch(windy.fetch())


def refresh_gdp() -> int:
    return _save_batch(gdp.fetch())


def _save_storm(item: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO storms(id,agency,season,number,name_en,name_zh,status,category,lat,lon,
                               pressure_hpa,wind_ms,motion_dir,motion_speed_kmh,observed_at,updated_at,raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              agency=excluded.agency, season=excluded.season, number=excluded.number,
              name_en=excluded.name_en, name_zh=excluded.name_zh, status=excluded.status,
              category=excluded.category, lat=excluded.lat, lon=excluded.lon,
              pressure_hpa=excluded.pressure_hpa, wind_ms=excluded.wind_ms,
              motion_dir=excluded.motion_dir, motion_speed_kmh=excluded.motion_speed_kmh,
              observed_at=excluded.observed_at, updated_at=excluded.updated_at, raw_json=excluded.raw_json
            """,
            (
                item["id"], item["agency"], item.get("season"), item.get("number"),
                item.get("name_en"), item.get("name_zh"), item.get("status"),
                item.get("category"), item.get("lat"), item.get("lon"),
                item.get("pressure_hpa"), item.get("wind_ms"),
                item.get("motion_dir"), item.get("motion_speed_kmh"),
                item.get("observed_at"), item.get("updated_at") or iso(),
                json_dumps({k: item.get(k) for k in ("raw", "name_zh", "number") if k in item}),
            ),
        )
        conn.execute(
            "DELETE FROM track_points WHERE storm_id=? AND agency=?",
            (item["id"], item["agency"]),
        )
        for p in item.get("points", []):
            conn.execute(
                """
                INSERT OR REPLACE INTO track_points
                  (storm_id,agency,valid_time,lat,lon,pressure_hpa,wind_ms,category,
                   wind_radii_json,forecast,issued_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["id"], item["agency"], p["valid_time"], p["lat"], p["lon"],
                    p.get("pressure_hpa"), p.get("wind_ms"), p.get("category"),
                    json_dumps(p.get("wind_radii") or {}), 0, p.get("issued_at") or item.get("updated_at"),
                ),
            )
        for p in item.get("forecasts", []):
            conn.execute(
                """
                INSERT OR REPLACE INTO track_points
                  (storm_id,agency,valid_time,lat,lon,pressure_hpa,wind_ms,category,
                   wind_radii_json,forecast,issued_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["id"], item["agency"], p["valid_time"], p["lat"], p["lon"],
                    p.get("pressure_hpa"), p.get("wind_ms"), p.get("category"),
                    json_dumps(p.get("wind_radii") or {}), 1, p.get("issued_at") or item.get("updated_at"),
                ),
            )
        conn.commit()


def refresh_tc() -> int:
    items = nmc.fetch_active()
    agency = "CMA"
    if not items:
        items = jma.fetch_active()
        agency = "JMA"
    if not items:
        return 0
    for item in items:
        _save_storm(item)
    # 成功抓取后，把同一机构不再活跃的旧台风标记为 stop。
    with connect() as conn:
        ids = [x["id"] for x in items]
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE storms SET status='stop' WHERE agency=? AND id NOT IN ({marks})",
            [agency, *ids],
        )
        conn.commit()
    return len(items)


def _frame_count() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) c FROM satellite_frames").fetchone()["c"]


def refresh_satellite_light() -> int:
    satellite.ensure_frames("ir", hours=config.SATELLITE_HOURS, max_new=2)
    satellite.ensure_frames("visible", hours=config.SATELLITE_HOURS, max_new=2)
    return _frame_count()


def refresh_satellite_bootstrap() -> int:
    satellite.ensure_frames("ir", hours=config.SATELLITE_HOURS, max_new=6)
    satellite.ensure_frames("visible", hours=config.SATELLITE_HOURS, max_new=6)
    return _frame_count()


def refresh_imerg() -> int:
    satellite.update_imerg_meta()
    with connect() as conn:
        row = conn.execute("SELECT frame_time FROM imerg_meta WHERE id=1").fetchone()
    return 1 if row else 0


def refresh_metrics_only() -> int:
    return analytics.refresh_metrics()


JOBS: list[tuple[str, int, Callable[[], Any]]] = [
    ("nmc", config.TC_INTERVAL, refresh_tc),
    ("metar", config.METAR_INTERVAL, refresh_metar),
    ("synop", config.SYNOP_INTERVAL, refresh_synop),
    ("ndbc", config.NDBC_INTERVAL, refresh_ndbc),
    ("ship", config.SHIP_INTERVAL, refresh_ship),
    ("windy", config.WINDY_INTERVAL, refresh_windy),
    ("gdp", config.GDP_INTERVAL, refresh_gdp),
    ("satellite", config.SATELLITE_INTERVAL, refresh_satellite_light),
    ("imerg", config.IMERG_INTERVAL, refresh_imerg),
]

_running: dict[str, bool] = {}
_scheduler_started = False
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="tcw")


def _run_safe(name: str, fn: Callable[[], Any]) -> None:
    if _running.get(name):
        return
    _running[name] = True
    try:
        run_job(name, fn)
    finally:
        _running[name] = False


def refresh_all() -> None:
    for name, _, fn in JOBS:
        try:
            _run_safe(name, fn)
        except Exception:
            log.exception("refresh_all %s failed", name)


def _scheduler_loop() -> None:
    last: dict[str, float] = {name: 0.0 for name, _, _ in JOBS}
    while True:
        now = time.time()
        for name, interval, fn in JOBS:
            if now - last.get(name, 0) >= interval and not _running.get(name):
                last[name] = now
                _executor.submit(_run_safe, name, fn)
        time.sleep(10)


def start_background() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    # 首屏优先：台风、METAR、当前卫星帧先在后台跑。
    _executor.submit(_run_safe, "nmc", refresh_tc)
    _executor.submit(_run_safe, "metar", refresh_metar)
    _executor.submit(_run_safe, "satellite", refresh_satellite_bootstrap)
    _executor.submit(_run_safe, "ship", refresh_ship)
    _executor.submit(_run_safe, "windy", refresh_windy)
    _executor.submit(_run_safe, "gdp", refresh_gdp)
    _executor.submit(_run_safe, "ndbc", refresh_ndbc)
    _executor.submit(_run_safe, "synop", refresh_synop_backfill)
    _executor.submit(_run_safe, "imerg", refresh_imerg)
    threading.Thread(target=_scheduler_loop, name="tcw-scheduler", daemon=True).start()
    log.info("background scheduler started")
