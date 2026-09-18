import asyncio
from pathlib import Path

from privasheet.web.app import create_app
from privasheet.web.settings import Settings, load_settings


class AsgiResponse:
    def __init__(self, status_code, headers, body):
        self.status_code = status_code
        self.headers = headers
        self.content = body
        self.text = body.decode("utf-8", errors="replace")


async def _asgi_request(app, method, path, headers=None):
    response = {"status": None, "headers": {}, "body": bytearray()}
    request_sent = False

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
            response["headers"] = {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in message["headers"]
            }
        elif message["type"] == "http.response.body":
            response["body"].extend(message.get("body", b""))

    input_headers = headers or {}
    raw_headers = (
        []
        if "host" in {key.lower() for key in input_headers}
        else [(b"host", b"testserver")]
    )
    for key, value in input_headers.items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    return AsgiResponse(
        response["status"], response["headers"], bytes(response["body"])
    )


def request(app, method, path, headers=None):
    return asyncio.run(_asgi_request(app, method, path, headers))


def make_app(**overrides):
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        allowed_hosts=("testserver", "example.test"),
        data_dir=Path("/tmp/privasheet-test"),
        base_url="http://testserver",
        model="test-model",
        document_timeout_s=30,
    )
    settings = settings.model_copy(update=overrides)
    return create_app(settings)


def test_pages_render_with_base_layout_and_nav():
    app = make_app()

    for path, title in [
        ("/", "Templates"),
        ("/templates/new", "New template"),
        ("/scan", "Scan batch"),
        ("/review", "Review"),
        ("/export", "Export"),
    ]:
        response = request(app, "GET", path)

        assert response.status_code == 200
        assert title in response.text
        assert "navbar" in response.text
        assert 'data-bs-theme="light"' in response.text
        assert "/static/vendor/bootstrap/bootstrap.min.css" in response.text
        assert "/static/app/confirm.js" in response.text


def test_static_vendor_and_app_assets_are_served():
    app = make_app()

    static_route = next(
        route for route in app.routes if getattr(route, "path", "") == "/static"
    )
    static_dir = Path(static_route.app.directory)
    bootstrap = static_dir / "vendor" / "bootstrap" / "bootstrap.min.css"
    react_setup = static_dir / "app" / "react-setup.js"

    assert static_route.name == "static"
    assert "Bootstrap" in bootstrap.read_text(encoding="utf-8")
    assert "htm.bind(React.createElement)" in react_setup.read_text(encoding="utf-8")


def test_security_headers_are_set():
    response = request(make_app(), "GET", "/")

    assert response.headers["content-security-policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_host_allowlist_rejects_unknown_hosts():
    app = make_app()

    response = request(app, "GET", "/", headers={"host": "attacker.test"})

    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"


def test_state_changing_requests_require_allowed_origin():
    app = make_app()

    accepted = request(
        app,
        "POST",
        "/templates/delete",
        headers={"origin": "http://example.test"},
    )
    rejected = request(
        app,
        "POST",
        "/templates/delete",
        headers={"origin": "http://evil.test"},
    )

    assert accepted.status_code == 303
    assert rejected.status_code == 403
    assert rejected.headers["content-security-policy"].startswith("default-src 'self'")


def test_settings_load_env_file_with_environment_overrides(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PRIVASHEET_HOST=0.0.0.0",
                "PORT=8123",
                "ALLOWED_HOSTS=localhost, example.test",
                f"DATA_DIR={tmp_path / 'data'}",
                "BASE_URL=https://example.test",
                "MODEL=env-model",
                "DOCUMENT_TIMEOUT_S=45",
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL", "override-model")

    settings = load_settings(env_file)

    assert settings.host == "0.0.0.0"
    assert settings.port == 8123
    assert settings.allowed_hosts == ("localhost", "example.test")
    assert settings.data_dir == tmp_path / "data"
    assert settings.base_url == "https://example.test"
    assert settings.model == "override-model"
    assert settings.document_timeout_s == 45


def test_settings_default_data_dir_is_installation_adjacent_temp(tmp_path, monkeypatch):
    for key in [
        "PRIVASHEET_HOST",
        "PORT",
        "ALLOWED_HOSTS",
        "DATA_DIR",
        "BASE_URL",
        "MODEL",
        "DOCUMENT_TIMEOUT_S",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = load_settings(tmp_path / "missing.env")

    assert settings.data_dir.name == "temp"
    assert settings.data_dir.parent.name == "privasheet"
