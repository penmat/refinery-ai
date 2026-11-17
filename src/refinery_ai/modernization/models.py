"""Versioned destination contracts, separate from executable recipe batches."""

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ObjectiveCategory = Literal["compatibility", "dependency", "source", "security"]


def _compatibility_effect() -> list[ObjectiveCategory]:
    return ["compatibility"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModernizationMode(StrEnum):
    MINIMAL = "minimal"
    RECOMMENDED = "recommended"
    FULL = "full"


def exact_version(value: str) -> str:
    if (
        not value
        or not value[0].isdigit()
        or any(c in value for c in "${}[](),+* /\\")
        or value.upper().endswith("SNAPSHOT")
        or re.search(r"(?:^|[.-])[xX](?:$|[.-])", value)
    ):
        raise ValueError("A concrete release version is required")
    return value


class EvidenceReference(Contract):
    id: str
    source: str
    detail: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AnalysisAvailability(Contract):
    analyzer: str
    status: Literal["completed", "partial", "unavailable", "off"]
    reason: str = ""
    fingerprint: str = ""


class TargetSelection(Contract):
    component: str
    version: str
    module: str = "."
    group: str | None = None
    artifact: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = ""

    _exact = field_validator("version")(exact_version)


class TransformationSpec(Contract):
    recipe: str
    artifact: str
    options: dict[str, str | int | bool] = Field(default_factory=dict)
    effects: list[ObjectiveCategory] = Field(default_factory=_compatibility_effect)


class CompletionPredicate(Contract):
    kind: Literal["version", "receipt", "finding"]
    component: str | None = None
    version: str | None = None
    module: str = "."
    analyzer: str | None = None
    finding_ids: list[str] = Field(default_factory=list)
    no_change_proves_satisfied: bool = False


class ModernizationObjective(Contract):
    id: str
    category: Literal["compatibility", "dependency", "source", "security"]
    rationale: str
    evidence_ids: list[str]
    depends_on: list[str] = Field(default_factory=list)
    target: TargetSelection | None = None
    required: bool = False
    disposition: Literal["executable", "advisory"] = "executable"
    transformations: list[TransformationSpec] = Field(default_factory=list)
    completion: CompletionPredicate
    advisory_ids: list[str] = Field(default_factory=list)
    affected_coordinates: list[str] = Field(default_factory=list)


class ModernizationIntentPlan(Contract):
    schema_version: Literal[1] = 1
    policy_version: Literal["1"] = "1"
    plan_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-zA-Z0-9]{1,128}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    repository: Path
    source_revision: str
    build_fingerprint: str
    source_fingerprint: str
    requested_jdk: int = Field(ge=8)
    mode: ModernizationMode = ModernizationMode.RECOMMENDED
    targets: list[TargetSelection] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    analysis: list[AnalysisAvailability] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    objectives: list[ModernizationObjective] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        evidence = {item.id for item in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError("Duplicate evidence IDs")
        seen: set[str] = set()
        for objective in self.objectives:
            if objective.id in seen or not set(objective.depends_on) <= seen:
                raise ValueError("Objectives must have unique IDs and dependency order")
            if not set(objective.evidence_ids) <= evidence:
                raise ValueError("Unknown objective evidence")
            if (
                objective.required
                and objective.disposition == "advisory"
                and not self.blocked_reasons
            ):
                raise ValueError("Required unexecutable objective must block the plan")
            target = objective.target
            if target is not None and not any(
                (target.component, target.group, target.artifact, target.module, target.version)
                == (t.component, t.group, t.artifact, t.module, t.version)
                for t in self.targets
            ):
                raise ValueError("Objective target is not a saved destination target")
            if objective.completion.kind == "version" and (
                target is None
                or objective.completion.component != target.component
                or objective.completion.version != target.version
            ):
                raise ValueError("Version completion must match the objective destination")
            if objective.completion.kind == "finding" and (
                not objective.completion.analyzer or not objective.completion.finding_ids
            ):
                raise ValueError("Finding completion requires an analyzer and finding IDs")
            seen.add(objective.id)
        for target in self.targets:
            if not set(target.evidence_ids) <= evidence:
                raise ValueError("Unknown target evidence")
            if target.component == "java" and target.version != str(self.requested_jdk):
                raise ValueError("Java target differs from requested JDK")
        return self
