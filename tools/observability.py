"""Structured logging via structlog.

Calling ``configure_logging()`` once at process start sets up structlog
plus a stdlib bridge so legacy ``logging.info(...)`` calls also flow
through the structured pipeline.

For tests and ad-hoc scripts, calling ``configure_logging()`` is optional
— structlog auto-configures on first use with default settings.
"""
from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

import structlog


def configure_logging(*, json_output: bool | None = None, level: str = "INFO") -> None:
    """Set up structlog + stdlib bridge.

    ``json_output``:
      * True  — single-line JSON (production / CI)
      * False — human-readable colored output (dev)
      * None  — auto: JSON if stdout isn't a TTY
    """
    if json_output is None:
        json_output = not sys.stdout.isatty()

    timestamper = structlog.processors.TimeStamper(fmt="iso")
    pre_chain: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
    ]

    renderer = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    # structlog config: pipeline ends with ProcessorFormatter.wrap_for_formatter
    # so the stdlib handler does the final rendering once.
    structlog.configure(
        processors=pre_chain
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def new_run_id() -> str:
    """Per-run identifier for end-to-end tracing."""
    return uuid.uuid4().hex[:12]


def bind_run_context(*, run_id: str, **kwargs: Any) -> None:
    structlog.contextvars.bind_contextvars(run_id=run_id, **kwargs)


def clear_run_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str | None = None):
    return structlog.get_logger(name) if name else structlog.get_logger()
