"""Deterministic planning and plan policy validation."""

from __future__ import annotations

from refinery_ai.models import (
    DiscoverySnapshot,
    ModernizationGoal,
    ModernizationPlan,
    ModernizationStep,
    StepStrategy,
)


def rule_based_plan(snapshot: DiscoverySnapshot, goal: ModernizationGoal) -> ModernizationPlan:
    """Create a conservative plan that remains useful without model credentials."""

    check = "./mvnw test" if snapshot.build_system.value == "maven" else "./gradlew test"
    if snapshot.build_system.value == "unknown":
        check = "manual verification"
    recipe = None
    if goal.target_java in {8, 11, 17, 21}:
        recipe = f"org.openrewrite.java.migrate.UpgradeToJava{goal.target_java}"
    return ModernizationPlan(
        goal=goal,
        summary="Establish a baseline, apply deterministic recipes, then review remaining gaps.",
        steps=[
            ModernizationStep(
                id="baseline",
                title="Establish a green baseline",
                strategy=StepStrategy.COMMAND,
                rationale="Migration results must be measured against a known baseline.",
                acceptance_checks=[check],
            ),
            ModernizationStep(
                id="rewrite",
                title="Apply approved OpenRewrite recipes",
                strategy=StepStrategy.OPENREWRITE,
                rationale="Deterministic transformations are preferred to generated edits.",
                depends_on=["baseline"],
                affected_files=[str(path) for path in snapshot.build_files],
                recipe=recipe,
                acceptance_checks=[check],
                risk="medium",
            ),
            ModernizationStep(
                id="review-gaps",
                title="Review remaining migration gaps",
                strategy=StepStrategy.AI,
                rationale="Reason about gaps that approved recipes cannot address.",
                depends_on=["rewrite"],
                affected_files=[str(path) for path in snapshot.build_files],
                acceptance_checks=[check],
                risk="medium",
            ),
        ],
    )


def validate_plan(plan: ModernizationPlan) -> None:
    """Reject duplicate, cyclic, or improperly ordered plan steps."""

    ids = {step.id for step in plan.steps}
    if len(ids) != len(plan.steps):
        raise ValueError("Plan step IDs must be unique")
    completed: set[str] = set()
    for step in plan.steps:
        unknown = set(step.depends_on) - ids
        if unknown:
            raise ValueError(f"Step {step.id} has unknown dependencies: {sorted(unknown)}")
        unmet = set(step.depends_on) - completed
        if unmet:
            raise ValueError(f"Step {step.id} is out of dependency order: {sorted(unmet)}")
        completed.add(step.id)
