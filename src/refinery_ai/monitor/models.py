"""Versioned, safe monitor API models."""

from typing import Literal

from pydantic import BaseModel, Field


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


class MonitorEvent(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    sequence: int = Field(ge=1)
    timestamp: str
    event_id: str
    execution_id: str
    phase: str
    lifecycle: Literal["started", "completed", "failed", "cancelled", "interrupted"]
    generation: int = 0
    outcome: str | None = None
    duration_seconds: float | None = None
    parent_run_id: str | None = None
    parent_execution_id: str | None = None
    profile: str | None = None
    plan_id: str | None = None
    step: int | None = None
    recipe: str | None = None
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
    diagnostics: list[str] = Field(default_factory=list)


class RunSnapshot(BaseModel):
    run: RunSummary
    events: list[MonitorEvent]
    topology: GraphTopology
    cursor: int
    diagnostics: list[str] = Field(default_factory=list)
