"""轻量反爬 / 反滥用中间件。

不依赖第三方账号，包含：
- 已知爬虫 / 空 UA / 扫描器 UA 拦截
- 按 IP + 路径类别的滑动窗口限速
- 可选 JS Cookie 挑战（TCW_CHALLENGE=1，默认开启）
- 图片防盗链 / 跨站帧请求拦截
- 安全响应头、robots.txt
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

COOKIE = "tcw_pass"
CHALLENGE_WINDOW = 180
PASS_TTL = 2 * 24 * 3600

TRUST_PROXY = os.getenv("TCW_TRUST_PROXY", "0") == "1"
CHALLENGE_ENABLED = os.getenv("TCW_CHALLENGE", "1") == "1"
SECRET = os.getenv("TCW_SECRET") or secrets.token_hex(32)

BOT_MARKERS = (
    "python-requests", "python-urllib", "scrapy", "curl/", "wget/",
    "go-http-client", "java/", "libwww", "httpclient", "axios/",
    "node-fetch", "okhttp", "crawler", "spider", "scraper", "bot/",
    "googlebot", "bingbot", "yandex", "baiduspider", "semrush",
    "ahrefs", "mj12bot", "dotbot", "petalbot", "bytespider",
    "sqlmap", "nikto", "nmap", "masscan", "zgrab", "nuclei",
)

BAD_PATH_PREFIXES = (
    "/.env", "/.git", "/wp-", "/wp/", "/phpmyadmin", "/admin",
    "/cgi-bin", "/xmlrpc", "/vendor/phpunit", "/actuator", "/server-status",
)

PUBLIC_PATHS = {
    "/api/health",
    "/robots.txt",
    "/favicon.ico",
    "/__tcw_verify",
}


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(self._hits) > 5000:
            for k in [x for x, v in self._hits.items() if not v]:
                self._hits.pop(k, None)
        return True


limiter = RateLimiter()


def _client_ip(request: Request) -> str:
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        cf = request.headers.get("cf-connecting-ip", "")
        if cf:
            return cf.strip()
    return request.client.host if request.client else "unknown"


def _sign(value: str) -> str:
    return hmac.new(SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_challenge(ip: str) -> str:
    ts = str(int(time.time()))
    return f"{ts}.{_sign(f'c|{ts}|{ip}')}"


def check_challenge(token: str, ip: str) -> bool:
    try:
        ts, sig = token.split(".", 1)
        if abs(time.time() - int(ts)) > CHALLENGE_WINDOW:
            return False
        return hmac.compare_digest(sig, _sign(f"c|{ts}|{ip}"))
    except Exception:
        return False


def make_pass() -> str:
    ts = str(int(time.time()))
    return f"{ts}.{_sign(f'p|{ts}')}"


def valid_pass(token: str) -> bool:
    try:
        ts, sig = token.split(".", 1)
        if abs(time.time() - int(ts)) > PASS_TTL:
            return False
        return hmac.compare_digest(sig, _sign(f"p|{ts}"))
    except Exception:
        return False


def _challenge_page(token: str) -> str:
    token_json = json.dumps(token)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#ffffff">
<title>安全检查 · BAMC 风眼</title>
<style>
  *{{box-sizing:border-box}} body{{margin:0;min-height:100vh;display:grid;place-items:center;
  background:#fff;color:#102a43;font:14px/1.5 Inter,system-ui,"Microsoft YaHei",sans-serif}}
  .box{{width:min(360px,86vw);padding:26px;border:1px solid #d9e2ec;border-radius:14px;
  box-shadow:0 18px 50px rgba(16,42,67,.1);text-align:center}}
  .globe{{width:46px;height:46px;margin:0 auto 12px;border-radius:50%;
  border:2px solid #0066cc;position:relative;background:#f5f9ff}}
  .globe:before,.globe:after{{content:"";position:absolute;inset:5px;border:1px solid #1e7bb8;
  border-radius:50%;opacity:.55}} .globe:after{{inset:5px 13px}}
  h1{{margin:0 0 4px;font-size:18px}} p{{margin:0;color:#627d98;font-size:12px}}
  .bar{{height:3px;margin:18px 0 12px;background:#eef4f8;border-radius:99px;overflow:hidden}}
  .bar i{{display:block;height:100%;width:35%;background:linear-gradient(90deg,#0066cc,#1e7bb8);
  border-radius:99px;animation:move 1.2s ease-in-out infinite alternate}}
  @keyframes move{{to{{transform:translateX(185%)}}}}
  .tip{{font-size:11px;color:#829ab1}} a{{color:#1e7bb8}}
</style>
</head>
<body>
  <div class="box">
    <div class="globe"></div>
    <h1>正在验证访问</h1>
    <p>BAMC 风眼 · TC WATCH</p>
    <div class="bar"><i></i></div>
    <p class="tip" id="tip">首次进入需要完成一次安全校验</p>
  </div>
<script>
(async () => {{
  try {{
    const r = await fetch("/__tcw_verify", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ token: {token_json} }})
    }});
    if (r.ok) {{ location.replace("/"); return; }}
    throw new Error("verify failed");
  }} catch (e) {{
    document.getElementById("tip").innerHTML = "验证失败，请刷新页面重试";
  }}
}})();
</script>
</body>
</html>"""


def _rate_class(path: str) -> tuple[str, int, int]:
    if path.startswith("/api/"):
        return "api", 240, 60
    if path.startswith("/frames/"):
        return "frames", 600, 60
    if path.startswith("/static/"):
        return "static", 420, 60
    if path == "/__tcw_verify":
        return "verify", 30, 60
    return "page", 60, 60


def _is_bad_ua(ua: str) -> bool:
    low = ua.lower()
    return any(marker in low for marker in BOT_MARKERS)


class AntiScrapeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        ip = _client_ip(request)
        ua = request.headers.get("user-agent", "")

        if path in PUBLIC_PATHS and path != "/__tcw_verify":
            response = await call_next(request)
            return self._headers(response, path, request)

        if path == "/__tcw_verify":
            kind, limit, window = _rate_class(path)
            if not limiter.allow(f"{ip}:{kind}", limit, window):
                return self._deny("Too Many Requests", 429, path, request, retry=window)
            response = await call_next(request)
            return self._headers(response, path, request)

        if any(path.startswith(p) for p in BAD_PATH_PREFIXES):
            return self._deny("Not Found", 404, path, request)

        if not ua or _is_bad_ua(ua):
            return self._deny("Forbidden", 403, path, request)

        # 防盗链：跨站 iframe / 图片直接拒绝；同站页面不受影响。
        if path.startswith("/frames/") and request.headers.get("sec-fetch-site") == "cross-site":
            return self._deny("Forbidden", 403, path, request)

        kind, limit, window = _rate_class(path)
        if not limiter.allow(f"{ip}:{kind}", limit, window):
            return self._deny("Too Many Requests", 429, path, request, retry=window)

        if CHALLENGE_ENABLED:
            passed = valid_pass(request.cookies.get(COOKIE, ""))
            if not passed:
                if path == "/" or path == "/index.html":
                    token = make_challenge(ip)
                    return HTMLResponse(
                        _challenge_page(token),
                        status_code=200,
                        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
                    )
                elif path.startswith("/api/") or path.startswith("/frames/"):
                    return self._deny("Forbidden", 403, path, request)

        response = await call_next(request)
        return self._headers(response, path, request)

    @staticmethod
    def _headers(response: Response, path: str, request: Request) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if path == "/" or path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @staticmethod
    def _deny(message: str, status: int, path: str, request: Request, retry: int | None = None) -> Response:
        wants_json = path.startswith("/api/") or path.startswith("/frames/")
        if wants_json:
            response: Response = JSONResponse({"detail": message}, status_code=status)
        else:
            response = PlainTextResponse(message, status_code=status)
        if retry:
            response.headers["Retry-After"] = str(retry)
        response.headers["X-Robots-Tag"] = "noindex"
        return AntiScrapeMiddleware._headers(response, path, request)


async def verify_challenge(request: Request) -> Response:
    try:
        body = await request.json()
        token = str(body.get("token", ""))
    except Exception:
        return JSONResponse({"detail": "Bad Request"}, status_code=400)
    ip = _client_ip(request)
    if not check_challenge(token, ip):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    response = JSONResponse({"ok": True})
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        COOKIE,
        make_pass(),
        max_age=PASS_TTL,
        httponly=True,
        samesite="lax",
        secure=(proto == "https"),
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def robots() -> Response:
    return PlainTextResponse("User-agent: *\nDisallow: /\n", headers={"Cache-Control": "no-store"})
