"""Application logging helpers with safe command rendering."""

from __future__ import annotations

import logging
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

LOGGER_NAME = "refinery_ai"
_SECRET_MARKERS = ("password", "passwd", "secret", "token", "api_key", "apikey")


def configure_logging(level: int = logging.INFO) -> None:
    """Configure concise stderr logging once for CLI execution."""

    logger = logging.getLogger(LOGGER_NAME)
    for existing in list(logger.handlers):
        if getattr(existing, "_refinery_cli_handler", False):
            logger.removeHandler(existing)
            existing.close()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler._refinery_cli_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def safe_command(command: Sequence[str]) -> str:
    """Render an argument vector while redacting common inline or following secrets."""

    rendered: list[str] = []
    redact_next = False
    for argument in command:
        if redact_next:
            rendered.append("[REDACTED]")
            redact_next = False
            continue
        key, separator, _ = argument.partition("=")
        normalized = key.casefold().replace("-", "_")
        sensitive = any(marker in normalized for marker in _SECRET_MARKERS)
        if separator and sensitive:
            rendered.append(f"{key}=[REDACTED]")
        else:
            rendered.append(argument)
            redact_next = sensitive and argument.startswith("-")
    return shlex.join(rendered)


def log_command(logger: logging.Logger, command: Sequence[str], *, cwd: Path | None = None) -> None:
    location = f" in {cwd}" if cwd is not None else ""
    logger.info("Running command%s: %s", location, safe_command(command))
