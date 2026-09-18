import asyncio
import json

from test_web_app import make_client

from privasheet.presets import PRESETS


async def post_json(app, path, payload):
    body = json.dumps(payload).encode()
    response = {"status": None, "body": bytearray()}
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
        elif message["type"] == "http.response.body":
            response["body"].extend(message.get("body", b""))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"origin", b"http://testserver")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return response["status"], json.loads(response["body"])


def test_presets_endpoint_returns_builtin_presets():
    response = make_client().get("/api/presets")

    assert response.status_code == 200
    presets = json.loads(response.text)["presets"]
    assert presets[0]["tag"] == PRESETS[0]["tag"]
    assert any(preset["tag"] == "#required" for preset in presets)


def test_template_validate_endpoint_returns_path_errors():
    app = make_client().app
    status, payload = asyncio.run(
        post_json(
            app,
            "/api/templates/validate",
            {
                "fields": [{"key": "date", "type": "date", "format": "bad"}],
                "tables": [
                    {
                        "key": "items",
                        "columns": [
                            {"key": "service_date", "type": "date", "format": "bad"}
                        ],
                    }
                ],
            },
        )
    )

    assert status == 200
    assert any(error.startswith("fields[0].format") for error in payload["errors"])
    assert any(
        error.startswith("tables[0].columns[0].format") for error in payload["errors"]
    )


def test_new_template_page_loads_field_editor_component():
    response = make_client().get("/templates/new")

    assert response.status_code == 200
    assert 'id="fieldEditorRoot"' in response.text
    assert "/static/app/field-editor.js" in response.text
