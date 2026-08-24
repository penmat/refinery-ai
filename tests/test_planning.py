from pathlib import Path

import pytest

from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    ModernizationGoal,
    ModernizationPlan,
    ModernizationStep,
    StepStrategy,
)
from refinery_ai.planning import rule_based_plan, validate_plan


def test_rule_plan_prefers_rewrite() -> None:
    snapshot = DiscoverySnapshot(repository=Path("/repo"), build_system=BuildSystem.MAVEN)
    plan = rule_based_plan(snapshot, ModernizationGoal(description="Upgrade Java", target_java=17))
    validate_plan(plan)
    assert plan.steps[1].strategy is StepStrategy.OPENREWRITE
    assert plan.steps[1].recipe == "org.openrewrite.java.migrate.UpgradeToJava17"


def test_rule_plan_leaves_unknown_target_for_review() -> None:
    snapshot = DiscoverySnapshot(repository=Path("/repo"), build_system=BuildSystem.GRADLE)
    plan = rule_based_plan(snapshot, ModernizationGoal(description="Upgrade Java", target_java=22))
    assert plan.steps[1].recipe is None


def test_rejects_unknown_dependency() -> None:
    plan = ModernizationPlan(
        goal=ModernizationGoal(description="Upgrade Java"),
        summary="bad plan",
        steps=[
            ModernizationStep(
                id="one",
                title="One",
                strategy=StepStrategy.COMMAND,
                rationale="test",
                depends_on=["missing"],
                acceptance_checks=["test"],
            )
        ],
    )
    with pytest.raises(ValueError, match="unknown dependencies"):
        validate_plan(plan)
