"""Versioned, safe monitor API models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from refinery_ai.assessment.models import Assessment, Category, Deduction


class TopologyNode(BaseModel):
    id: str
    label: str


class TopologyEdge(BaseModel):
    source: str
    target: str
    conditional: bool = False


class GraphTopology(BaseModel):
    schema_version: Literal[1] = 1
    kind: Literal["profile", "migration"]
    version: str
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]
    historical: bool = True


class PlannedMigration(BaseModel):
    recipe: str
    name: str


class AssessmentScore(BaseModel):
    assessment_id: str
    created_at: datetime
    profile_fingerprint: str
    completeness: float
    overall: float | None
    categories: dict[Category, float | None]
    deductions: list[Deduction]

    @classmethod
    def from_assessment(cls, assessment: Assessment) -> "AssessmentScore":
        return cls(
            assessment_id=assessment.assessment_id,
            created_at=assessment.created_at,
            profile_fingerprint=assessment.profile.fingerprint,
            completeness=assessment.completeness,
            overall=assessment.score.overall,
            categories=assessment.score.categories,
            deductions=sorted(assessment.score.deductions, key=lambda item: -item.points)[:3],
        )


class AnalysisSummary(BaseModel):
    assessment_score: AssessmentScore | None = None
    java_version: str | None = None
    spring_boot_version: str | None = None
    build_system: str | None = None
    frameworks: list[str] = Field(default_factory=list)


class MonitorEvent(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    sequence: int = Field(ge=1)
    timestamp: str
    event_id: str
    execution_id: str
    phase: str
    lifecycle: Literal["started", "completed", "failed", "cancelled", "interrupted", "usage"]
    generation: int = 0
    outcome: str | None = None
    analysis: AnalysisSummary | None = None
    failure_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_seconds: float | None = None
    child_run_id: str | None = None
    parent_run_id: str | None = None
    parent_execution_id: str | None = None
    profile: str | None = None
    plan_id: str | None = None
    step: int | None = None
    recipe: str | None = None
    migration_name: str | None = None
    total_steps: int | None = None
    migrations: list[PlannedMigration] | None = None
    repair_stage: str | None = None
    repair_attempt: int | None = None
    java: int | None = None
    checkpoint: str | None = None


class RunSummary(BaseModel):
    run_id: str
    kind: Literal["profile", "migration"]
    profile: str | None = None
    parent_run_id: str | None = None
    parent_execution_id: str | None = None
    status: str = "unknown"
    first_seen: str | None = None
    last_seen: str | None = None
    event_count: int = 0
    verified_recipes: int = 0
    recipe: str | None = None
    step_status: str | None = None
    failure_reason: str | None = None
    migration_name: str | None = None
    step: int | None = None
    total_steps: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    diagnostics: list[str] = Field(default_factory=list)


class RunSnapshot(BaseModel):
    run: RunSummary
    events: list[MonitorEvent]
    topology: GraphTopology
    cursor: int
    diagnostics: list[str] = Field(default_factory=list)
