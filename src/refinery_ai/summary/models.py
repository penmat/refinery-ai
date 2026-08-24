"""Typed contracts for deterministic project summaries."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from refinery_ai.models import ContextEvidence, ProviderRun


class ProjectOverview(BaseModel):
    name: str
    architecture_style: str
    build_system: str
    java_version: str | None = None
    frameworks: list[str] = Field(default_factory=list)
    module_count: int = 0
    production_java_files: int = 0
    test_java_files: int = 0


class ProjectStructure(BaseModel):
    modules: list[str] = Field(default_factory=list)
    top_level_directories: list[str] = Field(default_factory=list)
    source_roots: list[str] = Field(default_factory=list)
    resource_roots: list[str] = Field(default_factory=list)
    migration_locations: list[str] = Field(default_factory=list)


class ProminentComponent(BaseModel):
    id: str
    name: str
    kind: str
    relationship_count: int
    evidence: list[ContextEvidence] = Field(default_factory=list)


class ArchitectureSummary(BaseModel):
    components_by_kind: dict[str, int] = Field(default_factory=dict)
    prominent_components: list[ProminentComponent] = Field(default_factory=list)
    relationship_kinds: dict[str, int] = Field(default_factory=dict)
    primary_flow: str | None = None


class ExternalInterfaces(BaseModel):
    http_endpoints: list[str] = Field(default_factory=list)
    kafka_topics: list[str] = Field(default_factory=list)
    cache_regions: list[str] = Field(default_factory=list)
    redis_keyspaces: list[str] = Field(default_factory=list)


class DataArchitecture(BaseModel):
    engines: list[str] = Field(default_factory=list)
    connections: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    entity_mappings: int = 0
    table_references: int = 0
    direct_reads: int = 0
    direct_writes: int = 0


class ModernizationPosture(BaseModel):
    score: float | None = None
    evidence_completeness: float = 0
    category_scores: dict[str, float | None] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)


class EvidenceSummary(BaseModel):
    graph_path: Path
    schema_version: int
    created_at: datetime
    node_count: int
    edge_count: int
    provider_runs: list[ProviderRun] = Field(default_factory=list)
    graph_stale: bool = False


class SummaryObservation(BaseModel):
    severity: str
    category: str
    message: str
    evidence: list[ContextEvidence] = Field(default_factory=list)


class ProjectSummary(BaseModel):
    schema_version: str = "1.0"
    repository: Path
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    overview: ProjectOverview
    structure: ProjectStructure
    architecture: ArchitectureSummary
    interfaces: ExternalInterfaces
    data: DataArchitecture
    modernization: ModernizationPosture
    evidence: EvidenceSummary
    observations: list[SummaryObservation] = Field(default_factory=list)
