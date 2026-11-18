"""风眼 TC WATCH 后端配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("TCW_DATA_DIR", ROOT / "data")).resolve()
WEB_DIR = Path(os.getenv("TCW_WEB_DIR", ROOT / "web")).resolve()
DB_PATH = Path(os.getenv("TCW_DB_PATH", DATA_DIR / "tcwatch.db")).resolve()
FRAMES_DIR = Path(os.getenv("TCW_FRAMES_DIR", DATA_DIR / "frames")).resolve()
CACHE_DIR = Path(os.getenv("TCW_CACHE_DIR", DATA_DIR / "cache")).resolve()
LOG_DIR = Path(os.getenv("TCW_LOG_DIR", DATA_DIR / "logs")).resolve()
ISD_HISTORY = Path(os.getenv("TCW_ISD_HISTORY", ROOT / "isd-history.csv")).resolve()

# 监测范围：西、南、东、北
DOMAIN = (
    float(os.getenv("TCW_WEST", "100")),
    float(os.getenv("TCW_SOUTH", "5")),
    float(os.getenv("TCW_EAST", "160")),
    float(os.getenv("TCW_NORTH", "40")),
)

USER_AGENT = os.getenv(
    "TCW_USER_AGENT",
    "TC-WATCH/0.1 (local research dashboard; contact: local)",
)

# 更新周期（秒）
TC_INTERVAL = int(os.getenv("TCW_TC_INTERVAL", "300"))
METAR_INTERVAL = int(os.getenv("TCW_METAR_INTERVAL", "300"))
SYNOP_INTERVAL = int(os.getenv("TCW_SYNOP_INTERVAL", "600"))
NDBC_INTERVAL = int(os.getenv("TCW_NDBC_INTERVAL", "300"))
SHIP_INTERVAL = int(os.getenv("TCW_SHIP_INTERVAL", "600"))
WINDY_INTERVAL = int(os.getenv("TCW_WINDY_INTERVAL", "900"))
GDP_INTERVAL = int(os.getenv("TCW_GDP_INTERVAL", "600"))
WINDY_GRID_STEP = float(os.getenv("TCW_WINDY_GRID_STEP", "5"))
WINDY_COUNT = int(os.getenv("TCW_WINDY_COUNT", "100"))
SATELLITE_INTERVAL = int(os.getenv("TCW_SATELLITE_INTERVAL", "600"))
IMERG_INTERVAL = int(os.getenv("TCW_IMERG_INTERVAL", "1800"))

# 卫星帧
SATELLITE_HOURS = int(os.getenv("TCW_SATELLITE_HOURS", "3"))
SATELLITE_ZOOM = int(os.getenv("TCW_SATELLITE_ZOOM", "5"))
SATELLITE_WORKERS = int(os.getenv("TCW_SATELLITE_WORKERS", "10"))

# SYNOP 抓取窗口（小时），太短会漏报，太长会重复。
SYNOP_LOOKBACK_HOURS = int(os.getenv("TCW_SYNOP_LOOKBACK_HOURS", "4"))
# 在 ISD 元数据里出现的 WMO block；只抓这些 block，减少请求。
SYNOP_BLOCKS = tuple(
    int(x)
    for x in os.getenv(
        "TCW_SYNOP_BLOCKS",
        "45,46,47,48,52,53,54,56,57,58,59,91,96,98,99",
    ).split(",")
    if x.strip().isdigit()
)

HOST = os.getenv("TCW_HOST", "127.0.0.1")
PORT = int(os.getenv("TCW_PORT", "8787"))
LOG_LEVEL = os.getenv("TCW_LOG_LEVEL", "INFO").upper()


@dataclass(frozen=True)
class SatelliteChannel:
    key: str
    label: str
    layer: str
    matrix: str
    zoom: int


SATELLITE_CHANNELS = {
    "visible": SatelliteChannel(
        "visible",
        "可见光",
        "Himawari_AHI_Band3_Red_Visible_1km",
        "GoogleMapsCompatible_Level7",
        SATELLITE_ZOOM,
    ),
    "ir": SatelliteChannel(
        "ir",
        "红外",
        "Himawari_AHI_Band13_Clean_Infrared",
        "GoogleMapsCompatible_Level6",
        SATELLITE_ZOOM,
    ),
}

IMERG_LAYER = "IMERG_Precipitation_Rate_30min"
IMERG_MATRIX = "GoogleMapsCompatible_Level6"
GIBS_WMTS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"


def ensure_dirs() -> None:
    for path in (DATA_DIR, FRAMES_DIR, CACHE_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
    for ch in SATELLITE_CHANNELS:
        (FRAMES_DIR / ch).mkdir(parents=True, exist_ok=True)
