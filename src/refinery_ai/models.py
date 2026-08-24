"""Typed contracts shared by every factory stage."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


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


class KnowledgeGraphInfo(BaseModel):
    provider: str = "graphify"
    graph_path: Path
    report_path: Path | None = None
    node_count: int = 0
    edge_count: int = 0

    def summary(self) -> str:
        return f"Graphify graph with {self.node_count} nodes and {self.edge_count} edges"


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
    knowledge_graph: KnowledgeGraphInfo
    plan: ModernizationPlan
    steps: list[StepResult] = Field(default_factory=list)
