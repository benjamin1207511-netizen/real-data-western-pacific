"""NASA GIBS Himawari-9 卫星帧与 IMERG 元数据。

GIBS WMTS 已经做过地理配准，后端只负责：
  1. 按 100E–160E / 5N–40N 计算 Web Mercator 瓦片范围；
  2. 拼接、裁剪成一张 PNG；
  3. 记录时间戳，前端用 Leaflet ImageOverlay 播放。

瓦片模板：
  https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{time}/{matrix}/{z}/{y}/{x}.png
"""

from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from .. import config
from ..db import connect
from ..http import fetch_text
from ..util import iso, now_utc, parse_iso, utc_floor_10

log = logging.getLogger(__name__)

TILE = 256
CAPS_URL = f"{config.GIBS_WMTS}/1.0.0/WMTSCapabilities.xml"


def _global_px(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = TILE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(min(lat, 85.05112878), -85.05112878))
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _tile_url(channel: config.SatelliteChannel, frame_time: datetime, z: int, x: int, y: int) -> str:
    t = frame_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{config.GIBS_WMTS}/{channel.layer}/default/{t}/"
        f"{channel.matrix}/{z}/{y}/{x}.png"
    )


def _fetch_tile(url: str, retries: int = 3) -> Image.Image | None:
    import time
    from io import BytesIO

    for attempt in range(retries):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": config.USER_AGENT, "Accept": "image/png"},
                timeout=30,
            )
            if resp.status_code != 200 or not resp.content:
                if attempt < retries - 1:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                return None
            img = Image.open(BytesIO(resp.content)).convert("RGBA")
            if img.size != (TILE, TILE):
                img = img.resize((TILE, TILE))
            return img
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.35 * (attempt + 1))
    return None


def _latest_tile_url(channel: config.SatelliteChannel, z: int, x: int, y: int) -> str:
    return (
        f"{config.GIBS_WMTS}/{channel.layer}/default/default/"
        f"{channel.matrix}/{z}/{y}/{x}.png"
    )


def stitch(channel_key: str, frame_time: datetime) -> Path | None:
    channel = config.SATELLITE_CHANNELS[channel_key]
    west, south, east, north = config.DOMAIN
    z = channel.zoom
    px0, py0 = _global_px(north, west, z)
    px1, py1 = _global_px(south, east, z)
    tx0, ty0 = int(px0 // TILE), int(py0 // TILE)
    tx1, ty1 = int(px1 // TILE), int(py1 // TILE)
    cols, rows = tx1 - tx0 + 1, ty1 - ty0 + 1
    if cols <= 0 or rows <= 0:
        return None

    canvas = Image.new("RGBA", (cols * TILE, rows * TILE), (0, 0, 0, 0))
    jobs = []
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            jobs.append((tx, ty, _tile_url(channel, frame_time, z, tx, ty)))

    ok = 0
    missing: list[tuple[int, int]] = []
    with ThreadPoolExecutor(max_workers=config.SATELLITE_WORKERS) as pool:
        future_map = {pool.submit(_fetch_tile, url): (tx, ty) for tx, ty, url in jobs}
        for fut in as_completed(future_map):
            tx, ty = future_map[fut]
            try:
                img = fut.result()
            except Exception:
                img = None
            if img is None:
                missing.append((tx, ty))
                continue
            canvas.paste(img, ((tx - tx0) * TILE, (ty - ty0) * TILE), img)
            ok += 1

    # 并发抓取偶尔会漏 1–2 个瓦片；补抓一次 timed tile，再退回 GIBS 的“最新”瓦片。
    still_missing: list[tuple[int, int]] = []
    for tx, ty in missing:
        img = _fetch_tile(_tile_url(channel, frame_time, z, tx, ty), retries=2)
        if img is None:
            img = _fetch_tile(_latest_tile_url(channel, z, tx, ty), retries=2)
        if img is not None:
            canvas.paste(img, ((tx - tx0) * TILE, (ty - ty0) * TILE), img)
            ok += 1
        else:
            still_missing.append((tx, ty))
    missing = still_missing

    if ok == 0:
        return None
    # 仍缺瓦片时，用邻近瓦片的边缘像素填洞，避免出现透明方块。
    if ok < len(jobs):
        filled = canvas.copy()
        for tx, ty in missing:
            left = (tx - 1 - tx0) * TILE
            top = (ty - ty0) * TILE
            if left >= 0:
                strip = filled.crop((left, top, left + 4, top + TILE)).resize((TILE, TILE))
                canvas.paste(strip, ((tx - tx0) * TILE, top))
            else:
                canvas.paste(Image.new("RGBA", (TILE, TILE), (92, 100, 108, 255)), ((tx - tx0) * TILE, top))
    # 至少 25% 的瓦片有效，避免拼出半张图。
    if ok < max(1, int(len(jobs) * 0.25)):
        log.warning("satellite %s %s: only %s/%s tiles", channel_key, frame_time, ok, len(jobs))
        return None

    crop = (
        int(round(px0 - tx0 * TILE)),
        int(round(py0 - ty0 * TILE)),
        int(round(px1 - tx0 * TILE)),
        int(round(py1 - ty0 * TILE)),
    )
    out = canvas.crop(crop)
    # 上限 1800px，避免前端图片过大。
    if out.width > 1800:
        ratio = 1800 / out.width
        out = out.resize((1800, max(1, int(out.height * ratio))), Image.LANCZOS)

    filename = f"{frame_time.strftime('%Y%m%dT%H%MZ')}.png"
    path = config.FRAMES_DIR / channel_key / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path, "PNG", optimize=True)
    return path


def register_frame(channel_key: str, frame_time: datetime, path: Path) -> None:
    west, south, east, north = config.DOMAIN
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO satellite_frames
              (channel, frame_time, path, west, south, east, north, width, height, bytes, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(channel, frame_time) DO UPDATE SET
              path=excluded.path, width=excluded.width, height=excluded.height,
              bytes=excluded.bytes, created_at=excluded.created_at
            """,
            (
                channel_key,
                iso(frame_time),
                str(path.relative_to(config.DATA_DIR)).replace("\\", "/"),
                west, south, east, north,
                Image.open(path).width,
                Image.open(path).height,
                path.stat().st_size,
                iso(),
            ),
        )


def ensure_frames(channel_key: str, hours: int | None = None, max_new: int = 18) -> int:
    hours = hours or config.SATELLITE_HOURS
    now = now_utc()
    latest = utc_floor_10(now - timedelta(minutes=20))
    slots = [latest - timedelta(minutes=10 * i) for i in range(int(hours * 6) + 1)]
    made = 0
    for frame_time in slots:
        if made >= max_new:
            break
        filename = config.FRAMES_DIR / channel_key / f"{frame_time.strftime('%Y%m%dT%H%MZ')}.png"
        if filename.exists() and filename.stat().st_size > 1000:
            if not _registered(channel_key, frame_time):
                register_frame(channel_key, frame_time, filename)
            continue
        path = stitch(channel_key, frame_time)
        if path:
            register_frame(channel_key, frame_time, path)
            made += 1
            log.info("satellite frame %s %s", channel_key, iso(frame_time))
    return made


def _registered(channel_key: str, frame_time: datetime) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM satellite_frames WHERE channel=? AND frame_time=?",
            (channel_key, iso(frame_time)),
        ).fetchone()
        return bool(row)


def list_frames(channel: str, hours: int = 3) -> list[dict[str, Any]]:
    since = iso(now_utc() - timedelta(hours=hours))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT channel, frame_time, path, west, south, east, north, width, height
            FROM satellite_frames
            WHERE channel=? AND frame_time>=?
            ORDER BY frame_time ASC
            """,
            (channel, since),
        ).fetchall()
    return [
        {
            **dict(row),
            "url": f"/frames/{row['path'].split('frames/', 1)[-1]}",
        }
        for row in rows
    ]


def latest_frame(channel: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM satellite_frames WHERE channel=? ORDER BY frame_time DESC LIMIT 1",
            (channel,),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["url"] = f"/frames/{row['path'].split('frames/', 1)[-1]}"
    return data


def default_channel() -> str:
    from ..util import solar_elevation

    west, south, east, north = config.DOMAIN
    elev = solar_elevation((south + north) / 2, (west + east) / 2, now_utc())
    if elev < -3:
        return "ir"
    if latest_frame("visible"):
        return "visible"
    return "ir"


def get_imerg_time() -> str | None:
    """从 GIBS capabilities 里取 IMERG 当前默认时间。"""
    try:
        xml = fetch_text(CAPS_URL, timeout=45, retries=1)
        idx = xml.find(f"<ows:Identifier>{config.IMERG_LAYER}</ows:Identifier>")
        if idx < 0:
            return None
        block = xml[idx : idx + 60000]
        m = re.search(r"<Default>(.*?)</Default>", block)
        return m.group(1) if m else None
    except Exception as exc:  # noqa: BLE001
        log.debug("IMERG capabilities failed: %s", exc)
        return None


def update_imerg_meta() -> None:
    t = get_imerg_time()
    if not t:
        return
    with connect() as conn:
        conn.execute(
            "INSERT INTO imerg_meta(id, frame_time, updated_at) VALUES(1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET frame_time=excluded.frame_time, updated_at=excluded.updated_at",
            (t, iso()),
        )
