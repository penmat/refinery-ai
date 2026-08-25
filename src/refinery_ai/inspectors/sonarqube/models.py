"""SonarQube inspector contracts and typed failures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SonarQubeError(RuntimeError):
    """Base class for sanitized SonarQube failures."""


class SonarQubeUnavailable(SonarQubeError):
    """Raised when a required local capability is absent."""


class SonarQubeTimeout(SonarQubeError):
    """Raised when startup or analysis exceeds its bound."""


@dataclass(frozen=True)
class SonarServer:
    container_name: str
    url: str
    image: str
    image_digest: str | None = None


@dataclass(frozen=True)
class SonarScannerContext:
    repository: Path
    server_url: str
    token: str
    project_key: str
