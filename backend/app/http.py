"""HTTP 抓取：超时、重试、状态记录。"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from . import config
from .util import json_dumps

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "*/*",
}


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status = status
        self.url = url


def fetch(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 3,
    backoff: float = 1.2,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                url,
                headers={**HEADERS, **(headers or {})},
                params=params,
                timeout=timeout,
            )
            if resp.status_code >= 400:
                raise FetchError(
                    f"HTTP {resp.status_code} for {url}",
                    status=resp.status_code,
                    url=url,
                )
            return resp
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise FetchError(str(last_error), url=url)


def fetch_text(url: str, **kwargs: Any) -> str:
    resp = fetch(url, **kwargs)
    # 大多数源是 UTF-8；少数老报文是 latin-1。
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def fetch_json(url: str, **kwargs: Any) -> Any:
    resp = fetch(url, **kwargs)
    return resp.json()


def fetch_bytes(url: str, **kwargs: Any) -> bytes:
    return fetch(url, **kwargs).content
