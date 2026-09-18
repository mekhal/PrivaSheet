import asyncio
import json
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
        self.content = body
        self.text = body.decode("utf-8", errors="replace")


class CompatibleTestClient(TestClient):
    def request(self, method, url, *, headers=None, data=None, json=None, **kwargs):
        return asyncio.run(
            self._asgi_request(method, url, headers=headers, data=data, json_body=json)
        )

    async def _asgi_request(self, method, url, headers=None, data=None, json_body=None):
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

        body = b""
        content_type = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            content_type = "application/json"
        elif data is not None:
            body = "&".join(f"{key}={value}" for key, value in data).encode("ascii")
            content_type = "application/x-www-form-urlencoded"

        response = {"status": None, "headers": {}, "body": bytearray()}
        request_sent = False

        async def receive():
            nonlocal request_sent
            if request_sent:
                return {"type": "http.disconnect"}
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

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
        if content_type:
            input_headers["content-type"] = content_type
            input_headers["content-length"] = str(len(body))
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


def test_demo_export_page_renders_manifest_and_react_entry():
    response = make_client().get("/demo/export")

    assert response.status_code == 200
    assert "Export demo" in response.text
    assert 'id="exportSelectRoot"' in response.text
    assert "/static/app/export-select.js" in response.text
    assert "doc_passed" in response.text
    assert "needs_review" in response.text


def test_demo_export_downloads_selected_jsonl_in_manifest_order():
    response = make_client().post(
        "/api/demo/export",
        headers={"origin": "http://testserver"},
        json={"selected_document_ids": ["doc_reviewed", "doc_passed"]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["content-disposition"].endswith('.jsonl"')
    lines = [json.loads(line) for line in response.text.splitlines()]
    assert [line["document_id"] for line in lines] == ["doc_passed", "doc_reviewed"]
    assert [line["human_reviewed"] for line in lines] == [False, True]


def test_demo_export_rejects_disabled_selection():
    response = make_client().post(
        "/api/demo/export",
        headers={"origin": "http://testserver"},
        json={"selected_document_ids": ["doc_failed"]},
    )

    assert response.status_code == 400
    assert "doc_failed" in response.text
