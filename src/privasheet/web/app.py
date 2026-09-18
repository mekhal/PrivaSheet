"""FastAPI web shell."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import PlainTextResponse

from privasheet.web.settings import Settings, load_settings

STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_PORTS = {"http": 80, "https": 443}
SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
}


class LocalStaticFiles:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        if scope["method"] not in {"GET", "HEAD"}:
            return await self._send(send, 405, b"Method Not Allowed", "text/plain")

        path = unquote(scope.get("path", ""))
        root_path = scope.get("root_path", "")
        path = path.removeprefix(root_path).lstrip("/")
        file_path = (self.directory / path).resolve()
        if (
            "\x00" in path
            or self.directory not in file_path.parents
            or not file_path.is_file()
        ):
            return await self._send(send, 404, b"Not Found", "text/plain")

        body = b"" if scope["method"] == "HEAD" else file_path.read_bytes()
        media_type = (
            mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        )
        await self._send(send, 200, body, media_type, file_path.stat().st_size)

    async def _send(self, send, status_code, body, media_type, length=None) -> None:
        headers = [
            (b"content-type", media_type.encode("latin-1")),
            (b"content-length", str(len(body) if length is None else length).encode()),
        ]
        await send(
            {"type": "http.response.start", "status": status_code, "headers": headers}
        )
        await send({"type": "http.response.body", "body": body})


def _strip_port(host: str) -> str:
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    return host.split(":", 1)[0]


def _origin_tuple(origin: str) -> tuple[str, str, int | None] | None:
    parsed = urlparse(origin)
    if not parsed.scheme or not parsed.hostname:
        return None
    try:
        port = parsed.port or DEFAULT_PORTS.get(parsed.scheme)
    except ValueError:
        return None
    return (parsed.scheme, parsed.hostname, port)


def _expected_origin_tuple(request: Request) -> tuple[str, str, int | None] | None:
    host = request.headers.get("host", "")
    return _origin_tuple(f"{request.url.scheme}://{host}")


def _with_security_headers(response):
    response.headers.update(SECURITY_HEADERS)
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="PrivaSheet", docs_url=None, redoc_url=None)
    app.state.settings = settings

    web_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=web_dir / "templates")
    app.mount("/static", LocalStaticFiles(web_dir / "static"), name="static")

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        allowed_hosts = set(settings.allowed_hosts)
        request_host = _strip_port(request.headers.get("host", ""))
        if "*" not in allowed_hosts and request_host not in allowed_hosts:
            return _with_security_headers(
                PlainTextResponse("Host not allowed", status_code=403)
            )

        if request.method in STATE_CHANGING_METHODS:
            origin = request.headers.get("origin")
            if not origin or _origin_tuple(origin) != _expected_origin_tuple(request):
                return _with_security_headers(
                    PlainTextResponse("Origin not allowed", status_code=403)
                )

        response = await call_next(request)
        return _with_security_headers(response)

    def render(request: Request, template_name: str, title: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            template_name,
            {"title": title, "active": request.url.path, "settings": settings},
        )

    @app.get("/", response_class=HTMLResponse)
    async def templates_page(request: Request) -> HTMLResponse:
        return render(request, "templates.html", "Templates")

    @app.get("/templates/new", response_class=HTMLResponse)
    async def new_template_page(request: Request) -> HTMLResponse:
        return render(request, "new_template.html", "New template")

    @app.post("/templates/delete")
    async def delete_template() -> RedirectResponse:
        return RedirectResponse("/", status_code=303)

    @app.get("/scan", response_class=HTMLResponse)
    async def scan_page(request: Request) -> HTMLResponse:
        return render(request, "scan.html", "Scan batch")

    @app.get("/review", response_class=HTMLResponse)
    async def review_page(request: Request) -> HTMLResponse:
        return render(request, "review.html", "Review")

    @app.get("/export", response_class=HTMLResponse)
    async def export_page(request: Request) -> HTMLResponse:
        return render(request, "export.html", "Export")

    return app
