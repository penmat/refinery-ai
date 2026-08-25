"""Provider contracts for context-graph collection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from refinery_ai.context_graph.models import ContextEdge, ContextGraphInfo, ContextNode
from refinery_ai.models import DiscoverySnapshot


@dataclass(frozen=True)
class ContextRequest:
    repository: Path
    discovery: DiscoverySnapshot | None = None


@dataclass
class ContextFragment:
    provider: str
    nodes: list[ContextNode] = field(default_factory=list)
    edges: list[ContextEdge] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


class ContextProvider(Protocol):
    @property
    def name(self) -> str: ...

    def supports(self, discovery: DiscoverySnapshot | None) -> bool: ...

    def collect(self, request: ContextRequest) -> ContextFragment: ...


class ContextGraphBuildService(Protocol):
    """Minimal injected context-graph build boundary."""

    def build(self, repository: Path) -> ContextGraphInfo: ...
