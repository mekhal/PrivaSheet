import asyncio
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from privasheet.web.app import create_app
from privasheet.web.settings import Settings


class AsgiResponse:
    def __init__(self, status_code, headers, body):
        self.status_code = status_code
        self.headers = headers
        self.text = body.decode("utf-8", errors="replace")


class CompatibleTestClient(TestClient):
    def request(self, method, url, *, headers=None, **kwargs):
        return asyncio.run(self._asgi_request(method, url, headers=headers))

    async def _asgi_request(self, method, url, headers=None):
        parsed = urlparse(str(url))
        if parsed.netloc:
            scheme = parsed.scheme
            host = parsed.netloc
            path = parsed.path or "/"
            query_string = parsed.query.encode("ascii")
        else:
            base = urlparse(str(self.base_url))
            scheme = base.scheme
            host = base.netloc
            path = parsed.path or "/"
            query_string = parsed.query.encode("ascii")

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

        input_headers = {"host": host, **(headers or {})}
        raw_headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in input_headers.items()
        ]

        await self.app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.4"},
                "http_version": "1.1",
                "method": method,
                "scheme": scheme,
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": query_string,
                "headers": raw_headers,
                "client": ("127.0.0.1", 1234),
                "server": (host.rsplit(":", 1)[0], 80),
            },
            receive,
            send,
        )
        return AsgiResponse(
            response["status"], response["headers"], bytes(response["body"])
        )


def make_client(**overrides):
    settings = Settings(
        host="127.0.0.1",
        port=8765,
        allowed_hosts=("testserver",),
        data_dir=Path("/tmp/privasheet-test"),
        base_url="http://llm.test:11434",
        model="test-model",
        document_timeout_s=30,
    )
    settings = replace(settings, **overrides)
    return CompatibleTestClient(create_app(settings), follow_redirects=False)


def test_demo_overlay_page_renders_synthetic_overlay_fixture():
    response = make_client().get("/demo/overlay")

    assert response.status_code == 200
    assert "Overlay demo" in response.text
    assert 'id="boxOverlayRoot"' in response.text
    assert 'id="boxOverlayData"' in response.text
    assert "/static/app/box-overlay.js" in response.text
    assert "synthetic-page-1" in response.text
    assert "invoice_no" in response.text
    assert "p2-b1" in response.text


def test_review_page_wires_overlay_component():
    response = make_client().get("/review")

    assert response.status_code == 200
    assert "Review" in response.text
    assert 'id="boxOverlayRoot"' in response.text
    assert "/static/app/box-overlay.js" in response.text
    assert "assigned" in response.text
    assert "highlighted_box_ids" in response.text
