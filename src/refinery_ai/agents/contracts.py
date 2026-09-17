"""Framework-neutral contracts for model-backed Refinery planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from refinery_ai.models import DiscoverySnapshot, RecipePlan
from refinery_ai.recipes.catalog import RecipeCatalogSession


@dataclass(frozen=True)
class PlanningDependencies:
    """Evidence and catalog access available to a planning implementation."""

    snapshot: DiscoverySnapshot
    graph_summary: str
    graph_context: str = ""
    artifact_context: str = "No persisted project artifacts are available."
    planner_rules: str = "No additional planner rules are configured."
    recipe_catalog: RecipeCatalogSession = field(
        default_factory=lambda: RecipeCatalogSession([])
    )
    evidence_corpus: str = ""
    evidence_facts: list[str] = field(default_factory=list)
    migration_failure_context: str = "No previous migration failures are recorded."
    failed_recipes: frozenset[str] = frozenset()
    completed_recipes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PlanningRequest:
    """One planning invocation, independent of the underlying agent framework."""

    prompt: str
    dependencies: PlanningDependencies
    run_id: str
    plan_id: str | None = None


@runtime_checkable
class Planner(Protocol):
    """Produce a validated recipe plan from a prepared planning request."""

    def plan(self, request: PlanningRequest) -> RecipePlan: ...
