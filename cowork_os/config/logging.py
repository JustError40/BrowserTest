"""Structured logging setup for coworkOS using structlog."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

import structlog

# ─── Sensitive field masking ──────────────────────────────────────────────────

_API_KEY_PATTERN = re.compile(
    r"(sk|ant|claude)-[A-Za-z0-9_\-]{4,}",
    re.IGNORECASE,
)

_SENSITIVE_FIELDS = frozenset(
    {"api_key", "apikey", "token", "password", "secret", "authorization", "anthropic_api_key", "openai_api_key"}
)


def _mask_api_keys(text: str) -> str:
    """Replace API key patterns in a string with sk-***."""
    return _API_KEY_PATTERN.sub("sk-***", text)


def _mask_sensitive_processor(
    logger: logging.Logger,
    method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor: mask sensitive fields and values."""
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_FIELDS:
            event_dict[key] = "sk-***"
        elif isinstance(event_dict[key], str):
            event_dict[key] = _mask_api_keys(event_dict[key])
    # Also mask the event message itself
    if isinstance(event_dict.get("event"), str):
        event_dict["event"] = _mask_api_keys(event_dict["event"])
    return event_dict


# ─── Setup ────────────────────────────────────────────────────────────────────

_SETUP_DONE = False


def setup_logging(log_level: str | None = None) -> None:
    """Configure structlog with console (human-readable) and file (JSON Lines) sinks.

    Args:
        log_level: Override log level; if None, loads from Settings.
    """
    global _SETUP_DONE  # noqa: PLW0603

    # Resolve level
    if log_level is None:
        try:
            from cowork_os.config.settings import get_settings

            log_level = get_settings().log_level
        except Exception:
            log_level = "INFO"

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Ensure logs/ directory exists
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "app.jsonl"

    # ── shared pre-chain processors ──────────────────────────────────────────
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _mask_sensitive_processor,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── Console handler (human-readable) ─────────────────────────────────────
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        foreign_pre_chain=shared_processors,
    )
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(numeric_level)

    # ── File handler (JSON Lines) ─────────────────────────────────────────────
    json_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setFormatter(json_formatter)
    file_handler.setLevel(numeric_level)

    # ── Root logger ───────────────────────────────────────────────────────────
    root_logger = logging.getLogger()
    # Avoid duplicate handlers on repeated setup_logging() calls
    if not _SETUP_DONE:
        root_logger.handlers.clear()
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)
    root_logger.setLevel(numeric_level)

    # Silence noisy third-party libraries
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _SETUP_DONE = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Convenience wrapper around structlog.get_logger().

    Usage:
        log = get_logger(__name__)
        log.info("msg", key="value")
    """
    return structlog.get_logger(name)
