"""Versioned contracts for deterministic assessments."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AnalyzerStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"


class Category(StrEnum):
    PLATFORM = "platform"
    DEPENDENCIES = "dependencies"
    MAINTAINABILITY = "maintainability"
    RISK = "risk"
    ARCHITECTURE = "architecture"
    TESTS = "tests"
    BUILD = "build"


class ToolIdentity(BaseModel):
    name: str
    version: str
    configuration_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Diagnostic(BaseModel):
    level: str = "warning"
    message: str


class Finding(BaseModel):
    fingerprint: str
    analyzer: str
    rule: str
    category: Category
    severity: str
    message: str
    path: str | None = None
    line: int | None = None


class AnalyzerResult(BaseModel):
    analyzer: str
    status: AnalyzerStatus
    completeness: float = Field(ge=0, le=1)
    duration_seconds: float = Field(ge=0)
    tool: ToolIdentity
    metrics: dict[str, int | float | str | bool | None] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class Deduction(BaseModel):
    category: Category
    points: float = Field(ge=0)
    reason: str
    metric: str


class Score(BaseModel):
    overall: float | None = Field(default=None, ge=0, le=100)
    categories: dict[Category, float | None]
    deductions: list[Deduction] = Field(default_factory=list)


class AssessmentProfile(BaseModel):
    profile_id: str
    scoring_version: str
    fingerprint: str
    category_weights: dict[Category, float]
    analyzers: list[ToolIdentity]


class RepositoryIdentity(BaseModel):
    root: Path
    commit: str | None = None
    branch: str | None = None
    dirty: bool = False


class AssessmentScope(BaseModel):
    modules_included: list[str] = Field(default_factory=list)
    modules_excluded: list[str] = Field(default_factory=list)


class Assessment(BaseModel):
    schema_version: str = "1.0"
    assessment_id: str
    name: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    repository: RepositoryIdentity
    profile: AssessmentProfile
    scope: AssessmentScope
    completeness: float = Field(ge=0, le=1)
    score: Score
    metrics: dict[str, Any]
    findings: list[Finding]
    analyzers: dict[str, AnalyzerResult]
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class ComparisonCompatibility(StrEnum):
    COMPATIBLE = "compatible"
    PARTIALLY_COMPATIBLE = "partially_compatible"
    INCOMPATIBLE = "incompatible"


class MetricDelta(BaseModel):
    metric: str
    before: int | float
    after: int | float
    change: float


class AssessmentComparison(BaseModel):
    previous_id: str
    current_id: str
    compatibility: ComparisonCompatibility
    reason: str | None = None
    overall_change: float | None = None
    category_changes: dict[Category, float | None]
    metric_changes: list[MetricDelta]
    resolved_findings: list[Finding]
    introduced_findings: list[Finding]
