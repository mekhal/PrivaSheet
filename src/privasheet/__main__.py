"""Server entry point: python -m privasheet."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import uvicorn
from fastapi import FastAPI

from privasheet.pipeline.runtime import REFUSALS, PipelineRuntime
from privasheet.selfcheck import exit_code, format_table, run_checks
from privasheet.web.app import create_app
from privasheet.web.settings import Settings, load_settings


def build_app(settings: Settings) -> FastAPI:
    return create_app(settings, PipelineRuntime(settings))


def _refusal(app: FastAPI) -> Exception | None:
    error = getattr(app.state, "startup_error", None)
    return error if isinstance(error, REFUSALS) else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m privasheet",
        description="Start PrivaSheet, or check that everything it needs is ready.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print what is ready and what is not, then exit; starts nothing",
    )
    return parser


def _check() -> int:
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"privasheet: cannot read the settings: {exc}", file=sys.stderr)
        return 1
    results = run_checks(settings)
    print(format_table(results))
    return exit_code(results)


def _serve() -> int:
    settings = load_settings()
    app = build_app(settings)
    try:
        uvicorn.run(app, host=settings.host, port=settings.port)
    except BaseException:
        # uvicorn exits (SystemExit) when the lifespan start-up fails.
        if _refusal(app) is None:
            raise
    refusal = _refusal(app)
    if refusal is not None:
        print(f"privasheet: cannot start: {refusal}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] = ()) -> int:
    args = _parser().parse_args(argv)
    return _check() if args.check else _serve()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
