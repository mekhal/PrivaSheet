"""Server entry point: python -m privasheet."""

from __future__ import annotations

import sys

import uvicorn
from fastapi import FastAPI

from privasheet.pipeline.runtime import REFUSALS, PipelineRuntime
from privasheet.web.app import create_app
from privasheet.web.settings import Settings, load_settings


def build_app(settings: Settings) -> FastAPI:
    return create_app(settings, PipelineRuntime(settings))


def main() -> int:
    settings = load_settings()
    app = build_app(settings)
    try:
        uvicorn.run(app, host=settings.host, port=settings.port)
    except BaseException:
        error = getattr(app.state, "startup_error", None)
        if not isinstance(error, REFUSALS):
            raise
        print(f"privasheet: cannot start: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
