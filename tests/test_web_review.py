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


def test_review_page_wires_editor_overlay_and_synthetic_contract():
    response = make_client().get("/review?document_id=doc_passed")

    assert response.status_code == 200
    assert 'id="reviewEditorRoot"' in response.text
    assert 'id="boxOverlayRoot"' in response.text
    assert "/static/app/review-editor.js" in response.text
    assert "/static/app/box-overlay.js" in response.text
    assert '"revision": 1' in response.text
    assert '"total": {"value": "1284.00"}' in response.text
    assert '"text": "Total 1,284.00"' in response.text
    assert '"document_id": "doc_passed"' in response.text


def test_demo_review_page_uses_same_editor_fixture():
    response = make_client().get("/demo/review")

    assert response.status_code == 200
    assert "Review demo" in response.text
    assert 'id="reviewEditorData"' in response.text
    assert "AI_UNCERTAIN" in response.text
    assert "DUPLICATE_DOCUMENT" in response.text
