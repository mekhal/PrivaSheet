"""FastAPI web shell."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import PlainTextResponse

from privasheet.web.settings import Settings, load_settings

STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
}


def _strip_port(host: str) -> str:
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    return host.split(":", 1)[0]


def _origin_host(origin: str) -> str:
    parsed = urlparse(origin)
    return parsed.hostname or ""


def _with_security_headers(response):
    response.headers.update(SECURITY_HEADERS)
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="PrivaSheet", docs_url=None, redoc_url=None)
    app.state.settings = settings

    web_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=web_dir / "templates")
    app.mount("/static", StaticFiles(directory=web_dir / "static"), name="static")

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
            if origin and _origin_host(origin) not in allowed_hosts:
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
