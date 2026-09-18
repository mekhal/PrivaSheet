"""OpenAI-compatible local LLM client tests."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from privasheet.llm import (
    LlmClient,
    LlmConfigError,
    LlmInvalidResponse,
    LlmTimeout,
    LlmUnavailable,
)


@pytest.fixture
def local_llm_server():
    class LocalLlmHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.server.last_request = {
                "path": self.path,
                "headers": self.headers,
                "body": self.rfile.read(length),
            }

            if self.server.mode == "success":
                self._send_json(
                    200,
                    {"choices": [{"message": {"content": json.dumps({"answer": 42})}}]},
                )
            elif self.server.mode == "http_error":
                self._send_json(500, {"error": "server failed"})
            elif self.server.mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://example.com/v1/chat/completions")
                self.end_headers()
            elif self.server.mode == "invalid_content":
                self._send_json(
                    200,
                    {"choices": [{"message": {"content": "not json"}}]},
                )
            elif self.server.mode == "timeout":
                time.sleep(1.0)

        def log_message(self, format, *args):
            pass

        def _send_json(self, status, body):
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    class LocalLlmServer(ThreadingHTTPServer):
        daemon_threads = True

    server = LocalLlmServer(("127.0.0.1", 0), LocalLlmHandler)
    server.mode = "success"
    server.last_request = None
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_chat_json_posts_openai_compatible_request_and_parses_content(local_llm_server):
    client = LlmClient(
        f"http://127.0.0.1:{local_llm_server.server_port}", "local-model"
    )

    result = client.chat_json([{"role": "user", "content": "extract"}])

    assert result == {"answer": 42}
    request = local_llm_server.last_request
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["Content-Type"] == "application/json"
    body = json.loads(request["body"].decode("utf-8"))
    assert body == {
        "model": "local-model",
        "messages": [{"role": "user", "content": "extract"}],
        "response_format": {"type": "json_object"},
    }


def test_http_error_is_unavailable(local_llm_server):
    local_llm_server.mode = "http_error"
    client = LlmClient(
        f"http://127.0.0.1:{local_llm_server.server_port}", "local-model"
    )

    with pytest.raises(LlmUnavailable):
        client.chat_json([{"role": "user", "content": "extract"}])


def test_redirect_is_refused(local_llm_server):
    local_llm_server.mode = "redirect"
    client = LlmClient(
        f"http://127.0.0.1:{local_llm_server.server_port}", "local-model"
    )

    with pytest.raises(LlmUnavailable):
        client.chat_json([{"role": "user", "content": "extract"}])


def test_invalid_content_json_is_invalid_response(local_llm_server):
    local_llm_server.mode = "invalid_content"
    client = LlmClient(
        f"http://127.0.0.1:{local_llm_server.server_port}", "local-model"
    )

    with pytest.raises(LlmInvalidResponse):
        client.chat_json([{"role": "user", "content": "extract"}])


def test_timeout_is_timeout(local_llm_server):
    local_llm_server.mode = "timeout"
    client = LlmClient(
        f"http://127.0.0.1:{local_llm_server.server_port}", "local-model"
    )

    with pytest.raises(LlmTimeout):
        client.chat_json([{"role": "user", "content": "extract"}], timeout=0.01)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://192.0.2.1:8080",
        "http://example.com:8080",
        "http://[2001:db8::1]:8080",
    ],
)
def test_non_loopback_hosts_are_refused(base_url):
    with pytest.raises(LlmConfigError):
        LlmClient(base_url, "local-model")
