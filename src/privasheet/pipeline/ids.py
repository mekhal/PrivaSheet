"""Sortable identifiers for pipeline records."""

from __future__ import annotations

import secrets
import threading
import time

_LOCK = threading.Lock()
_LAST_MILLISECOND = -1


def new_id(prefix: str) -> str:
    """Return a process-monotonic, lexicographically sortable id."""

    global _LAST_MILLISECOND

    now_ms = time.time_ns() // 1_000_000
    with _LOCK:
        if now_ms <= _LAST_MILLISECOND:
            now_ms = _LAST_MILLISECOND + 1
        _LAST_MILLISECOND = now_ms
    return f"{prefix}_{now_ms:013d}_{secrets.token_hex(8)}"
