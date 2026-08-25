"""Persisted context-graph models."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, JsonValue


class ContextEvidence(BaseModel):
    provider: str
    file: Path | None = None
    line: int | None = Field(default=None, ge=1)
    detail: str | None = None


class ContextNode(BaseModel):
    id: str
    kind: str
    name: str
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    sources: list[ContextEvidence] = Field(default_factory=list)


class ContextEdge(BaseModel):
    source: str
    target: str
    kind: str
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    sources: list[ContextEvidence] = Field(default_factory=list)


class ProviderRun(BaseModel):
    provider: str
    status: str = "completed"
    node_count: int = 0
    edge_count: int = 0
    diagnostics: list[str] = Field(default_factory=list)


class ContextGraph(BaseModel):
    schema_version: int = 1
    repository: Path
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    repository_fingerprint: str | None = None
    nodes: list[ContextNode] = Field(default_factory=list)
    edges: list[ContextEdge] = Field(default_factory=list)
    provider_runs: list[ProviderRun] = Field(default_factory=list)


class ContextGraphInfo(BaseModel):
    provider: str = "context-graph"
    graph_path: Path
    report_path: Path | None = None
    node_count: int = 0
    edge_count: int = 0
    schema_version: int = 1
    provider_runs: list[ProviderRun] = Field(default_factory=list)

    def summary(self) -> str:
        providers = ", ".join(run.provider for run in self.provider_runs) or "none"
        return (
            f"Context graph with {self.node_count} nodes and {self.edge_count} edges "
            f"from providers: {providers}"
        )
