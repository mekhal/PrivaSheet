"""OpenAI-compatible local LLM client tests."""

import json

import pytest

from privasheet import llm
from privasheet.llm import (
    LlmClient,
    LlmConfigError,
    LlmInvalidResponse,
    LlmTimeout,
    LlmUnavailable,
)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body
        if self._body is None:
            self._body = {
                "choices": [{"message": {"content": json.dumps({"answer": 42})}}],
            }

    def read(self):
        return json.dumps(self._body).encode("utf-8")


class FakeConnection:
    response = FakeResponse()
    request_args = None
    request_kwargs = None
    init_args = None
    error = None

    def __init__(self, *args, **kwargs):
        type(self).init_args = args, kwargs

    def request(self, *args, **kwargs):
        if type(self).error is not None:
            raise type(self).error
        type(self).request_args = args
        type(self).request_kwargs = kwargs

    def getresponse(self):
        return type(self).response

    def close(self):
        pass


@pytest.fixture
def fake_connection(monkeypatch):
    class Connection(FakeConnection):
        response = FakeResponse()
        request_args = None
        request_kwargs = None
        init_args = None
        error = None

    monkeypatch.setattr(llm.http.client, "HTTPConnection", Connection)
    return Connection


def test_chat_json_posts_openai_compatible_request_and_parses_content(fake_connection):
    client = LlmClient("http://127.0.0.1:8080", "local-model")

    result = client.chat_json([{"role": "user", "content": "extract"}])

    assert result == {"answer": 42}
    assert fake_connection.init_args == (
        ("127.0.0.1", 8080),
        {"timeout": 30.0},
    )
    assert fake_connection.request_args[:2] == ("POST", "/v1/chat/completions")
    body = json.loads(fake_connection.request_kwargs["body"].decode("utf-8"))
    assert body == {
        "model": "local-model",
        "messages": [{"role": "user", "content": "extract"}],
        "response_format": {"type": "json_object"},
    }


def test_http_error_is_unavailable(fake_connection):
    fake_connection.response = FakeResponse(status=500)
    client = LlmClient("http://127.0.0.1:8080", "local-model")

    with pytest.raises(LlmUnavailable):
        client.chat_json([{"role": "user", "content": "extract"}])


def test_redirect_is_refused(fake_connection):
    fake_connection.response = FakeResponse(status=302)
    client = LlmClient("http://127.0.0.1:8080", "local-model")

    with pytest.raises(LlmUnavailable):
        client.chat_json([{"role": "user", "content": "extract"}])


def test_invalid_content_json_is_invalid_response(fake_connection):
    fake_connection.response = FakeResponse(
        body={"choices": [{"message": {"content": "not json"}}]}
    )
    client = LlmClient("http://127.0.0.1:8080", "local-model")

    with pytest.raises(LlmInvalidResponse):
        client.chat_json([{"role": "user", "content": "extract"}])


def test_timeout_is_timeout(fake_connection):
    fake_connection.error = TimeoutError("slow")
    client = LlmClient("http://127.0.0.1:8080", "local-model")

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
