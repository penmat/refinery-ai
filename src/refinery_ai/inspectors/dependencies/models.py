"""Dependency-inspection contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class DependencyAnalysisError(RuntimeError):
    """A dependency graph or vulnerability service could not be used."""


class DependencyAnalysisTimeout(DependencyAnalysisError):
    """Dependency analysis exceeded its configured deadline."""


@dataclass(frozen=True, order=True)
class ResolvedDependency:
    group: str
    artifact: str
    version: str
    scope: str | None = None
    packaging: str | None = None
    classifier: str | None = None
    direct: bool = False
    module: str = "."
    dependency_path: tuple[str, ...] = ()

    @property
    def coordinate(self) -> str:
        return f"{self.group}:{self.artifact}:{self.version}"


@dataclass(frozen=True)
class Vulnerability:
    id: str
    modified: str | None
    summary: str | None
    severity: str
    aliases: list[str] = field(default_factory=list)
    affected: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    withdrawn: str | None = None
    retrieved_at: str | None = None
