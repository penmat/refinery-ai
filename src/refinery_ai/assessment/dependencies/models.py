"""Internal dependency-analysis contracts."""

from __future__ import annotations

from dataclasses import dataclass


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

    @property
    def coordinate(self) -> str:
        return f"{self.group}:{self.artifact}:{self.version}"


@dataclass(frozen=True)
class Vulnerability:
    id: str
    modified: str | None
    summary: str | None
    severity: str
