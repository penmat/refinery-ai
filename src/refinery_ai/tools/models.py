"""Typed, JSON-compatible contracts returned by repository evidence tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from refinery_ai.assessment.models import AnalyzerResult, Finding
from refinery_ai.context_graph.models import ContextEdge, ContextNode, ProviderRun


class EvidenceAvailability(BaseModel):
    available: bool = True
    reason: str | None = None


class InspectorSummary(BaseModel):
    inspector: str
    status: str
    completeness: float
    metric_names: list[str] = Field(default_factory=list)
    finding_count: int = 0
    diagnostic_count: int = 0


class RepositoryOverview(EvidenceAvailability):
    repository: str
    build_system: str
    java_version: str | None = None
    frameworks: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    declared_dependency_count: int = 0
    git_branch: str | None = None
    dirty: bool = False
    assessment_id: str | None = None
    assessment_created_at: datetime | None = None
    assessment_completeness: float | None = None
    assessment_score: float | None = None
    inspectors: list[InspectorSummary] = Field(default_factory=list)
    graph_available: bool = False
    graph_created_at: datetime | None = None
    graph_node_count: int = 0
    graph_edge_count: int = 0
    graph_providers: list[ProviderRun] = Field(default_factory=list)
    graph_fingerprint: str | None = None


class InspectorResult(EvidenceAvailability):
    inspector: str
    assessment_id: str | None = None
    result: AnalyzerResult | None = None
    findings_truncated: bool = False
    total_findings: int = 0
    returned_findings: int = 0


class FindingSearchResult(EvidenceAvailability):
    assessment_id: str | None = None
    matched_count: int = 0
    returned_count: int = 0
    truncated: bool = False
    findings: list[Finding] = Field(default_factory=list)


class ContextSearchResult(EvidenceAvailability):
    graph_created_at: datetime | None = None
    repository_fingerprint: str | None = None
    matched_node_count: int = 0
    returned_node_count: int = 0
    matched_edge_count: int = 0
    returned_edge_count: int = 0
    truncated: bool = False
    nodes: list[ContextNode] = Field(default_factory=list)
    edges: list[ContextEdge] = Field(default_factory=list)


class ContextNeighborhoodResult(EvidenceAvailability):
    node_id: str
    graph_created_at: datetime | None = None
    repository_fingerprint: str | None = None
    depth: int = 1
    matched_node_count: int = 0
    returned_node_count: int = 0
    matched_edge_count: int = 0
    returned_edge_count: int = 0
    truncated: bool = False
    nodes: list[ContextNode] = Field(default_factory=list)
    edges: list[ContextEdge] = Field(default_factory=list)


class SymbolSearchResult(EvidenceAvailability):
    graph_created_at: datetime | None = None
    repository_fingerprint: str | None = None
    matched_count: int = 0
    returned_count: int = 0
    truncated: bool = False
    symbols: list[ContextNode] = Field(default_factory=list)


class SymbolListResult(SymbolSearchResult):
    kind: str | None = None
    available_kinds: dict[str, int] = Field(default_factory=dict)


class SymbolExplanation(EvidenceAvailability):
    symbol_id: str
    graph_created_at: datetime | None = None
    repository_fingerprint: str | None = None
    symbol: ContextNode | None = None
    incoming: list[ContextEdge] = Field(default_factory=list)
    outgoing: list[ContextEdge] = Field(default_factory=list)
    related_symbols: list[ContextNode] = Field(default_factory=list)
    truncated: bool = False


class SemanticRelationship(BaseModel):
    relationship_type: str
    direction: str
    symbol: ContextNode
    relationship: ContextEdge


class SemanticRelationshipResult(EvidenceAvailability):
    symbol_id: str
    relationship: str
    coverage: str = "relationship-level"
    warning: str | None = None
    graph_created_at: datetime | None = None
    repository_fingerprint: str | None = None
    matched_count: int = 0
    returned_count: int = 0
    truncated: bool = False
    results: list[SemanticRelationship] = Field(default_factory=list)


JsonObject = dict[str, Any]
