import asyncio
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from privasheet.presets import PRESETS
from privasheet.web.app import create_app
from privasheet.web.settings import Settings


class JsonTestClient(TestClient):
    def request(self, method, url, *, headers=None, json=None, **kwargs):
        return asyncio.run(
            self._asgi_request(method, url, headers=headers, json_body=json)
        )

    async def _asgi_request(self, method, url, headers=None, json_body=None):
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
        input_headers = {"host": host, **(headers or {})}
        if json_body is not None:
            body = json.dumps(json_body).encode()
            input_headers.setdefault("content-type", "application/json")
            input_headers.setdefault("content-length", str(len(body)))

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
        return Response(
            response["status"], response["headers"], bytes(response["body"])
        )


class Response:
    def __init__(self, status_code, headers, body):
        self.status_code = status_code
        self.headers = headers
        self.text = body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


def make_client(**overrides):
    settings = Settings(
        host="127.0.0.1",
        port=8765,
        allowed_hosts=("testserver", "example.test"),
        data_dir=Path("/tmp/privasheet-test"),
        base_url="http://llm.test:11434",
        model="test-model",
        document_timeout_s=30,
    )
    settings = replace(settings, **overrides)
    return JsonTestClient(create_app(settings), follow_redirects=False)


def test_presets_endpoint_returns_builtin_presets():
    response = make_client().get("/api/presets")

    assert response.status_code == 200
    payload = response.json()
    assert payload["presets"][0]["tag"] == PRESETS[0]["tag"]
    assert any(preset["tag"] == "#line_items" for preset in payload["presets"])
    assert any(preset["tag"] == "#required" for preset in payload["presets"])


def test_template_validate_endpoint_returns_path_errors():
    response = make_client().post(
        "/api/templates/validate",
        headers={"origin": "http://testserver"},
        json={
            "fields": [
                {
                    "key": "invoice_date",
                    "type": "date",
                    "required": True,
                    "key_label": True,
                    "description": "Invoice date.",
                    "format": "bad",
                    "hint": {"labels": ["Invoice Date"]},
                }
            ],
            "tables": [
                {
                    "key": "line_items",
                    "required": True,
                    "description": "One row per item.",
                    "columns": [
                        {"key": "service_date", "type": "date", "format": "bad"}
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    errors = response.json()["errors"]
    assert any(error.startswith("fields[0].format") for error in errors)
    assert any(error.startswith("tables[0].columns[0].format") for error in errors)


def test_new_template_page_loads_field_editor_component():
    response = make_client().get("/templates/new")

    assert response.status_code == 200
    assert 'id="fieldEditorRoot"' in response.text
    assert "/static/app/field-editor.js" in response.text
