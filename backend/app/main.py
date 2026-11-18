"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, ingest
from .api import router
from .db import init_db

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tcw")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    init_db()
    ingest.start_background()
    log.info("TC WATCH started: %s", config.DOMAIN)
    yield


app = FastAPI(
    title="风眼 TC WATCH",
    description="西北太平洋热带气旋实况监测（5–40N / 100–160E）",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router)

if config.WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")
if config.FRAMES_DIR.exists():
    app.mount("/frames", StaticFiles(directory=str(config.FRAMES_DIR)), name="frames")


@app.get("/")
def index():
    path = config.WEB_DIR / "index.html"
    if not path.exists():
        return {"message": "frontend not built", "docs": "/docs"}
    return FileResponse(path)


@app.get("/favicon.ico")
def favicon():
    path = config.WEB_DIR / "favicon.svg"
    if path.exists():
        return FileResponse(path)
    return FileResponse(config.WEB_DIR / "index.html")
