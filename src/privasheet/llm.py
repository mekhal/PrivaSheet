"""Loopback-only OpenAI-compatible LLM client."""

import http.client
import ipaddress
import json
import socket
from urllib.parse import urlparse


class LlmError(Exception):
    """Base class for LLM client errors."""


class LlmConfigError(LlmError):
    """Raised when the LLM client is configured unsafely."""


class LlmUnavailable(LlmError):
    """Raised when the LLM service cannot satisfy the request."""


class LlmTimeout(LlmUnavailable):
    """Raised when the LLM service times out."""


class LlmInvalidResponse(LlmError):
    """Raised when the LLM service returns malformed JSON data."""


_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_DEFAULT_TIMEOUT = 30.0


class LlmClient:
    """Small OpenAI-compatible chat client restricted to loopback hosts."""

    def __init__(self, base_url, model, timeout=_DEFAULT_TIMEOUT):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise LlmConfigError("base_url must use http or https")
        if parsed.hostname is None:
            raise LlmConfigError("base_url must include a host")

        host = parsed.hostname.lower()
        if host not in _ALLOWED_HOSTS:
            raise LlmConfigError("LLM host must be loopback-only")

        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        _verify_loopback_resolution(host, port)

        self._scheme = parsed.scheme
        self._host = host
        self._port = port
        self._model = model
        self._timeout = timeout

    def chat_json(self, messages, timeout=None):
        """Send chat messages and parse the first JSON object response."""

        body = json.dumps(
            {
                "model": self._model,
                "messages": messages,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        request_timeout = self._timeout if timeout is None else timeout
        _verify_loopback_resolution(self._host, self._port)
        conn_class = (
            http.client.HTTPSConnection
            if self._scheme == "https"
            else http.client.HTTPConnection
        )
        connection = conn_class(self._host, self._port, timeout=request_timeout)
        try:
            connection.request(
                "POST", "/v1/chat/completions", body=body, headers=headers
            )
            response = connection.getresponse()
            response_body = response.read()
        except TimeoutError as exc:
            raise LlmTimeout("LLM request timed out") from exc
        except (http.client.HTTPException, OSError) as exc:
            raise LlmUnavailable("LLM service is unavailable") from exc
        finally:
            connection.close()

        if response.status != 200:
            raise LlmUnavailable(f"LLM service returned HTTP {response.status}")

        try:
            envelope = json.loads(response_body.decode("utf-8"))
            content = envelope["choices"][0]["message"]["content"]
            parsed_content = json.loads(content)
        except (
            KeyError,
            IndexError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise LlmInvalidResponse("LLM response is not valid JSON") from exc

        if not isinstance(parsed_content, dict):
            raise LlmInvalidResponse("LLM response content must be a JSON object")
        return parsed_content


def _verify_loopback_resolution(host, port):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise LlmConfigError("LLM host could not be resolved") from exc

    if not infos:
        raise LlmConfigError("LLM host could not be resolved")

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise LlmConfigError("LLM host resolved to a non-IP address") from exc
        if not ip.is_loopback:
            raise LlmConfigError("LLM host must resolve only to loopback addresses")
