"""Typed contracts shared by every factory stage."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field, JsonValue, model_validator


class BuildSystem(StrEnum):
    MAVEN = "maven"
    GRADLE = "gradle"
    UNKNOWN = "unknown"


class StepStrategy(StrEnum):
    OPENREWRITE = "openrewrite"
    AI = "ai"
    COMMAND = "command"
    MANUAL_REVIEW = "manual_review"


class RunStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class Dependency(BaseModel):
    group: str
    artifact: str
    version: str | None = None
    scope: str | None = None


class Module(BaseModel):
    name: str
    path: Path
    java_files: int = 0
    test_files: int = 0


class DiscoverySnapshot(BaseModel):
    repository: Path
    build_system: BuildSystem
    build_files: list[Path] = Field(default_factory=list)
    modules: list[Module] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    java_version: str | None = None
    frameworks: list[str] = Field(default_factory=list)
    git_branch: str | None = None
    dirty: bool = False
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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


# Compatibility name for integrations compiled against the 0.1 contract.
KnowledgeGraphInfo = ContextGraphInfo


class ModernizationGoal(BaseModel):
    description: str = Field(min_length=3)
    target_java: int | None = Field(default=None, ge=8)


class ModernizationStep(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str
    strategy: StepStrategy
    rationale: str
    depends_on: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    recipe: str | None = None
    acceptance_checks: list[str] = Field(default_factory=list, min_length=1)
    risk: str = "low"

    @model_validator(mode="after")
    def recipe_matches_strategy(self) -> ModernizationStep:
        if self.recipe and self.strategy is not StepStrategy.OPENREWRITE:
            raise ValueError("Only OpenRewrite steps may specify a recipe")
        return self


class ModernizationPlan(BaseModel):
    goal: ModernizationGoal
    summary: str
    steps: list[ModernizationStep] = Field(min_length=1)


class PatchProposal(BaseModel):
    """A model-proposed unified diff; the factory decides whether to apply it."""

    summary: str
    unified_diff: str
    affected_files: list[str]
    confidence: float = Field(ge=0, le=1)


class RepairProposal(PatchProposal):
    diagnosis: str


class VerificationResult(BaseModel):
    command: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float

    @property
    def green(self) -> bool:
        return self.exit_code == 0


class StepResult(BaseModel):
    step_id: str
    status: RunStatus
    message: str
    verification: VerificationResult | None = None
    attempts: int = 0


class RunReport(BaseModel):
    run_id: str
    status: RunStatus
    snapshot: DiscoverySnapshot
    context_graph: ContextGraphInfo = Field(
        validation_alias=AliasChoices("context_graph", "knowledge_graph")
    )
    plan: ModernizationPlan
    steps: list[StepResult] = Field(default_factory=list)

    @property
    def knowledge_graph(self) -> ContextGraphInfo:
        """Compatibility accessor for reports created before ContextGraph."""

        return self.context_graph
