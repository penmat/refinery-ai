from pathlib import Path

from refinery_ai.context_graph.models import ContextGraphInfo
from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    MigrationRun,
    RecipePlan,
    RecipePriority,
    RecipeRecommendation,
    RunReport,
    RunStatus,
)
from refinery_ai.profile_run import ProfileRunner, dependency_inputs_fingerprint
from refinery_ai.profiles import load_profile
from refinery_ai.workflows.profile import build_profile_graph


def recommendation(recipe: str) -> RecipeRecommendation:
    return RecipeRecommendation(
        recipe=recipe,
        reason="needed",
        evidence=["test"],
        expected_benefit="modernized",
        priority=RecipePriority.HIGH,
        confidence=1.0,
    )


def report(repository: Path, run_id: str, recipes: list[str]) -> RunReport:
    return RunReport(
        run_id=run_id,
        profile="spring-boot-3.2-java-17",
        status=RunStatus.PLANNED,
        snapshot=DiscoverySnapshot(
            repository=repository,
            build_system=BuildSystem.MAVEN,
            java_version="17",
            spring_boot_version="3.2.0",
        ),
        context_graph=ContextGraphInfo(graph_path=repository / "graph.json"),
        plan=RecipePlan(recommendations=[recommendation(recipe) for recipe in recipes]),
    )


def test_dependency_fingerprint_only_tracks_dependency_inputs(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project />")
    original = dependency_inputs_fingerprint(tmp_path)
    (tmp_path / "Example.java").write_text("class Example {}")
    assert dependency_inputs_fingerprint(tmp_path) == original
    (tmp_path / "pom.xml").write_text("<project><dependencies /></project>")
    assert dependency_inputs_fingerprint(tmp_path) != original


def test_profile_graph_exposes_deterministic_workflow_nodes(tmp_path: Path) -> None:
    runner = ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=lambda run_sonar: True,
        plan=lambda: report(tmp_path, "complete", []),
        migrate=lambda plan_id, checkpoint: MigrationRun(
            plan_id=plan_id, status=RunStatus.COMPLETED
        ),
    )

    assert set(build_profile_graph(runner).get_graph().nodes) == {
        "__start__",
        "analyze",
        "plan",
        "migrate",
        "__end__",
    }


def test_profile_runner_loops_until_plan_is_empty_and_skips_unchanged_sonar(
    tmp_path: Path,
) -> None:
    (tmp_path / "pom.xml").write_text("<project />")
    analyses: list[bool] = []
    plans = iter(
        [
            report(tmp_path, "plan-1", ["example.Upgrade"]),
            report(tmp_path, "plan-2", []),
        ]
    )

    result = ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=lambda run_sonar: not analyses.append(run_sonar),
        plan=lambda: next(plans),
        migrate=lambda plan_id, checkpoint: MigrationRun(
            plan_id=plan_id,
            status=RunStatus.COMPLETED,
            published_revision="abc123",
        ),
    ).run()

    assert result.status is RunStatus.COMPLETED
    assert result.cycles == 2
    assert analyses == [False, False]


def test_profile_runner_carries_verified_checkpoint_to_next_migration_cycle(
    tmp_path: Path,
) -> None:
    (tmp_path / "pom.xml").write_text("<project />")
    plans = iter(
        [
            report(tmp_path, "plan-1", ["example.One"]),
            report(tmp_path, "plan-2", ["example.Two"]),
            report(tmp_path, "complete", []),
        ]
    )
    checkpoints = []

    def migrate(plan_id, checkpoint):
        checkpoints.append(checkpoint)
        return MigrationRun(
            plan_id=plan_id,
            status=RunStatus.COMPLETED,
            published_revision=f"revision-{plan_id}",
            verified_java=8,
        )

    result = ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=lambda run_sonar: True,
        plan=lambda: next(plans),
        migrate=migrate,
    ).run()

    assert result.status is RunStatus.COMPLETED
    assert checkpoints[0] is None
    assert checkpoints[1].revision == "revision-plan-1"
    assert checkpoints[1].java == 8


def test_profile_runner_reruns_sonar_after_dependency_change(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text("<project />")
    analyses: list[bool] = []
    plans = iter(
        [
            report(tmp_path, "plan-1", ["example.Upgrade"]),
            report(tmp_path, "plan-2", []),
        ]
    )

    def migrate(plan_id: str, checkpoint) -> MigrationRun:
        del checkpoint
        pom.write_text("<project><dependency /></project>")
        return MigrationRun(
            plan_id=plan_id,
            status=RunStatus.COMPLETED,
            published_revision="abc123",
        )

    ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=lambda run_sonar: not analyses.append(run_sonar),
        plan=lambda: next(plans),
        migrate=migrate,
    ).run()

    assert analyses == [False, False]


def test_profile_runner_rechecks_targets_when_migration_makes_no_change(tmp_path: Path) -> None:
    plans = iter(
        [
            report(tmp_path, "plan-1", ["example.Upgrade"]),
            report(tmp_path, "complete", []),
        ]
    )
    result = ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=lambda run_sonar: True,
        plan=lambda: next(plans),
        migrate=lambda plan_id, checkpoint: MigrationRun(
            plan_id=plan_id,
            status=RunStatus.COMPLETED,
        ),
    ).run()

    assert result.status is RunStatus.COMPLETED
    assert result.cycles == 2


def test_profile_runner_does_not_complete_with_unsatisfied_structured_target(
    tmp_path: Path,
) -> None:
    incomplete = report(tmp_path, "empty", [])
    incomplete.snapshot.spring_boot_version = "3.0.13"

    result = ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=lambda run_sonar: True,
        plan=lambda: incomplete,
        migrate=lambda plan_id, checkpoint: MigrationRun(
            plan_id=plan_id, status=RunStatus.COMPLETED
        ),
    ).run()

    assert result.status is RunStatus.NEEDS_REVIEW
    assert "spring_boot 3.0.13 -> 3.2" in result.message


def test_profile_runner_recovers_when_retained_sonar_evidence_is_missing(
    tmp_path: Path,
) -> None:
    (tmp_path / "pom.xml").write_text("<project />")
    profile = load_profile("spring-boot-3.2-java-17")
    first = ProfileRunner(
        tmp_path,
        profile,
        analyze=lambda run_sonar: True,
        plan=lambda: report(tmp_path, "complete", []),
        migrate=lambda plan_id, checkpoint: MigrationRun(
            plan_id=plan_id, status=RunStatus.COMPLETED
        ),
    )
    first.run()

    analyses: list[bool] = []

    def analyze(run_sonar: bool) -> bool:
        analyses.append(run_sonar)
        return run_sonar

    ProfileRunner(
        tmp_path,
        profile,
        analyze=analyze,
        plan=lambda: report(tmp_path, "complete-again", []),
        migrate=lambda plan_id, checkpoint: MigrationRun(
            plan_id=plan_id, status=RunStatus.COMPLETED
        ),
    ).run()

    assert analyses == [False]
