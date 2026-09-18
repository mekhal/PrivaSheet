"""Configuration loading for the web application."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ENV_KEYS = (
    "PRIVASHEET_HOST",
    "PORT",
    "ALLOWED_HOSTS",
    "DATA_DIR",
    "BASE_URL",
    "MODEL",
    "DOCUMENT_TIMEOUT_S",
)


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    allowed_hosts: tuple[str, ...]
    data_dir: Path
    base_url: str
    model: str
    document_timeout_s: int

    def model_copy(self, *, update: dict[str, Any] | None = None) -> Settings:
        """Small compatibility helper mirroring pydantic's test-friendly API."""
        return replace(self, **(update or {}))


def _default_data_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    return root / "temp"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            values[key] = value
    return values


def _split_hosts(value: str) -> tuple[str, ...]:
    return tuple(host.strip() for host in value.split(",") if host.strip())


def load_settings(env_file: str | Path = ".env") -> Settings:
    file_values = _parse_env_file(Path(env_file))
    values = {key: os.environ.get(key, file_values.get(key)) for key in ENV_KEYS}

    host = values["PRIVASHEET_HOST"] or "127.0.0.1"
    port = int(values["PORT"] or "8000")
    allowed_hosts = _split_hosts(
        values["ALLOWED_HOSTS"] or "localhost,127.0.0.1,testserver"
    )
    data_dir = Path(values["DATA_DIR"]) if values["DATA_DIR"] else _default_data_dir()
    base_url = values["BASE_URL"] or f"http://{host}:{port}"
    model = values["MODEL"] or "local"
    document_timeout_s = int(values["DOCUMENT_TIMEOUT_S"] or "120")

    return Settings(
        host=host,
        port=port,
        allowed_hosts=allowed_hosts,
        data_dir=data_dir,
        base_url=base_url,
        model=model,
        document_timeout_s=document_timeout_s,
    )
