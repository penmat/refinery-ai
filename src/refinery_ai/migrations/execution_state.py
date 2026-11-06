"""Versioned values shared by migration execution, persistence and recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from refinery_ai.models import (
    MigrationRun,
    PersistedMigrationPlan,
    RecipeRecommendation,
    StepResult,
    VerificationResult,
)


class MigrationPolicy(BaseModel):
    resumable: bool = True
    repair_mode: str = "disabled"
    repair_backend: str = "internal"
    repair_model: str | None = None
    coding_agent_model: str | None = None
    coding_agent_provider: str | None = None
    pi_executable: str = "pi"
    ollama_base_url: str | None = None
    max_repair_attempts: int = 3
    max_deterministic_repair_attempts: int = 4
    stages: list[tuple[str, int, bool]] = Field(default_factory=list)
    timeout: int = 900
    output_limit: int = 20_000


class MigrationRequest(BaseModel):
    plan_id: str | None = None
    apply: bool = False
    publish: bool = True
    expected_profile: str | None = None
    trusted_baseline_revision: str | None = None
    trusted_baseline_java: int | None = None
    resume_run_id: str | None = None
    resume_bound_run: bool = False


class RunManifest(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    repository: Path
    state_directory: Path
    plan: PersistedMigrationPlan
    starting_revision: str
    policy: MigrationPolicy
    publish: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def plan_digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.plan.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()


class BaselineReceipt(BaseModel):
    schema_version: Literal[1] = 1
    revision: str
    java: int | None
    verification: VerificationResult
    artifacts: Path | None = None


class StepReceipt(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    plan_digest: str
    step: int
    recipe: RecipeRecommendation
    base_revision: str
    checkpoint: str
    generation: int
    java: int | None
    status: Literal["completed"] = "completed"
    execution: VerificationResult
    verification: VerificationResult
    workspace: Path
    result: StepResult


class PublicationIntent(BaseModel):
    schema_version: Literal[1] = 1
    starting_revision: str
    target_revision: str
    completed: bool = False


class RecoveryPoint(BaseModel):
    results: list[StepResult] = Field(default_factory=list)
    baseline: BaselineReceipt | None = None
    next_step: int = 1
    revision: str
    java: int | None = None
    generation: int = 1
    result: MigrationRun | None = None


class WorkspaceState(BaseModel):
    path: Path
    artifact_directory: Path
    base_revision: str


class MigrationExecutionState(BaseModel):
    schema_version: Literal[1] = 1
    plan: PersistedMigrationPlan
    run_id: str
    apply: bool
    publish: bool
    generation: int = 0
    index: int = 1
    current_java: int | None = None
    starting_revision: str | None = None
    current_revision: str | None = None
    published_revision: str | None = None
    baseline_verification: VerificationResult | None = None
    baseline_artifacts: Path | None = None
    results: list[StepResult] = Field(default_factory=list)
    status: str = "completed"
    workspace: WorkspaceState | None = None
    execution: VerificationResult | None = None
    build: VerificationResult | None = None
    candidate: VerificationResult | None = None
    repairs: list[VerificationResult] = Field(default_factory=list)
    stage_index: int = 0
    attempt: int = 0
    pending: bool = False
    outcome: str | None = None
    failure_reason: str = ""
    failure_message: str = ""
