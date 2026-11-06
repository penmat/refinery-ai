"""Versioned values shared by migration execution, persistence and recovery."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from refinery_ai.models import MigrationRun, PersistedMigrationPlan, RecipeRecommendation, StepResult, VerificationResult


class MigrationPolicy(BaseModel):
    repair_mode: str = 'disabled'
    repair_backend: str = 'internal'
    repair_model: str | None = None
    coding_agent_model: str | None = None
    coding_agent_provider: str | None = None
    pi_executable: str = 'pi'
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

    @computed_field
    @property
    def plan_digest(self) -> str:
        return hashlib.sha256(json.dumps(self.plan.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()


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
    status: Literal['completed'] = 'completed'
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
