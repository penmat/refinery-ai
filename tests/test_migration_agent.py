from pathlib import Path

from refinery_ai.agents.migration import MigrationAgent
from refinery_ai.migrations.openrewrite import _infer_recipe_artifact
from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    ExecutionType,
    PersistedMigrationPlan,
    RecipeRecommendation,
    RunStatus,
    VerificationResult,
)
from refinery_ai.planning_artifacts import MigrationPlanStore


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, int]] = []

    def run(self, snapshot, step, *, apply: bool, timeout: int) -> VerificationResult:
        self.calls.append((step.recipe, apply, timeout))
        return VerificationResult(command=["rewrite"], exit_code=0, duration_seconds=0)


def test_migration_agent_loads_latest_plan_and_dispatches_by_type(tmp_path: Path) -> None:
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.UpgradeToJava17",
        reason="Upgrade Java.",
        evidence=["goal.target_java=17"],
        expected_benefit="Java 17 support.",
        priority="high",
        confidence=0.9,
    )
    plan = PersistedMigrationPlan(
        plan_id="plan1",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[step],
    )
    (store.root / "plan1.json").write_text(plan.model_dump_json())
    runner = RecordingRunner()
    builds: list[list[str]] = []

    def build_verifier(repository, command, **kwargs):
        builds.append(command)
        return VerificationResult(command=command, exit_code=0, duration_seconds=0)

    result = MigrationAgent(
        store,
        runners={ExecutionType.OPENREWRITE: runner},
        timeout=42,
        build_verifier=build_verifier,
    ).run(apply=True)

    assert result.status is RunStatus.COMPLETED
    assert runner.calls == [(step.recipe, True, 42)]
    assert builds == [["mvn", "test"]]
    assert result.steps[0].execution is not None
    assert result.steps[0].verification is not None


def test_migration_agent_stops_at_recipe_with_failing_post_build(tmp_path: Path) -> None:
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    first = RecipeRecommendation(
        recipe="recipe.one",
        reason="First.",
        evidence=["first"],
        expected_benefit="First benefit.",
        priority="high",
        confidence=0.9,
    )
    second = first.model_copy(update={"recipe": "recipe.two"})
    plan = PersistedMigrationPlan(
        plan_id="plan2",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[first, second],
    )
    (store.root / "plan2.json").write_text(plan.model_dump_json())
    runner = RecordingRunner()
    build_count = 0

    def build_verifier(repository, command, **kwargs):
        nonlocal build_count
        build_count += 1
        return VerificationResult(
            command=command, exit_code=0 if build_count == 1 else 1, duration_seconds=0
        )

    result = MigrationAgent(
        store,
        runners={ExecutionType.OPENREWRITE: runner},
        build_verifier=build_verifier,
    ).run("plan2", apply=True)

    assert result.status is RunStatus.FAILED
    assert build_count == 2
    assert result.steps[0].status is RunStatus.COMPLETED
    assert result.steps[1].message == "Post-migration build failed: recipe.two"


def test_old_spring_plan_infers_rewrite_spring_artifact() -> None:
    assert _infer_recipe_artifact("org.openrewrite.java.springdoc.SpringFoxToSpringDoc") == (
        "org.openrewrite.recipe:rewrite-spring:RELEASE"
    )
