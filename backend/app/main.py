"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, ingest, security
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


PRODUCTION = os.getenv("TCW_ENV", "development") == "production"

app = FastAPI(
    title="风眼 TC WATCH",
    description="西北太平洋热带气旋实况监测（5–40N / 100–160E）",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if PRODUCTION else "/docs",
    redoc_url=None if PRODUCTION else "/redoc",
    openapi_url=None if PRODUCTION else "/openapi.json",
)

origins = [x.strip() for x in os.getenv("TCW_ALLOWED_ORIGINS", "").split(",") if x.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

app.add_middleware(security.AntiScrapeMiddleware)

app.include_router(router)


@app.post("/__tcw_verify", include_in_schema=False)
async def tcw_verify(request: Request):
    return await security.verify_challenge(request)


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    return security.robots()

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
