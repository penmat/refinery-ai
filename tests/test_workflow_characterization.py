import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from refinery_ai.context_graph.models import ContextGraphInfo
from refinery_ai.migrations.runner import MigrationRunner
from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    ExecutionType,
    MigrationRun,
    PersistedMigrationPlan,
    RecipePlan,
    RecipePriority,
    RecipeRecommendation,
    RunReport,
    RunStatus,
    VerificationResult,
)
from refinery_ai.planning_artifacts import MigrationPlanStore
from refinery_ai.profile_run import ProfileRunner, ProfileRunResult
from refinery_ai.profiles import load_profile

FIXTURES = Path(__file__).parent / "fixtures" / "workflow_characterization"
FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _recommendation(recipe: str = "example.Upgrade") -> RecipeRecommendation:
    return RecipeRecommendation(
        recipe=recipe,
        reason="The repository requires this migration.",
        evidence=["discovery.java_version=11"],
        expected_benefit="The repository reaches the requested target.",
        priority=RecipePriority.HIGH,
        confidence=0.9,
    )


def _snapshot(repository: Path) -> DiscoverySnapshot:
    return DiscoverySnapshot(
        repository=repository,
        build_system=BuildSystem.MAVEN,
        build_files=[Path("pom.xml")],
        java_version="17",
        spring_boot_version="3.2.0",
        frameworks=["Spring"],
        git_branch="lang-graph",
        discovered_at=FIXED_TIME,
    )


def _normalized(value: Any, repository: Path) -> Any:
    """Normalize only values intentionally allocated at runtime."""

    if isinstance(value, dict):
        return {
            key: (
                "<run-id>"
                if key == "run_id" and item is not None
                else _normalized(item, repository)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalized(item, repository) for item in value]
    if isinstance(value, str):
        return value.replace(str(repository), "<repository>")
    return value


def _assert_golden(name: str, actual: Any) -> None:
    expected = json.loads((FIXTURES / name).read_text())
    assert actual == expected, (
        f"Actual:\n{json.dumps(actual, indent=2, sort_keys=True)}\n"
        f"Expected:\n{json.dumps(expected, indent=2, sort_keys=True)}"
    )


def test_public_workflow_models_keep_their_serialized_contract(tmp_path: Path) -> None:
    plan = RecipePlan(recommendations=[_recommendation()])
    migration = MigrationRun(
        plan_id="plan-1",
        status=RunStatus.COMPLETED,
        run_id="migration-run-1",
        starting_revision="abc123",
        final_revision="def456",
        published_revision="def456",
        verified_java=17,
    )
    profile = ProfileRunResult(
        profile="spring-boot-3.2-java-17",
        status=RunStatus.COMPLETED,
        cycles=2,
        message="Profile complete; the planner found no remaining migrations.",
    )
    report = RunReport(
        run_id="plan-1",
        profile="spring-boot-3.2-java-17",
        status=RunStatus.PLANNED,
        snapshot=_snapshot(tmp_path),
        context_graph=ContextGraphInfo(
            graph_path=tmp_path / ".refinery/context/context-graph.json",
            provider="context-graph",
        ),
        plan=plan,
    )

    _assert_golden(
        "public-models.json",
        _normalized(
            {
                "recipe_plan": plan.model_dump(mode="json"),
                "migration_run": migration.model_dump(mode="json"),
                "profile_run_result": profile.model_dump(mode="json"),
                "run_report": report.model_dump(mode="json"),
            },
            tmp_path,
        ),
    )


class _PreviewRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        snapshot: DiscoverySnapshot,
        step: RecipeRecommendation,
        *,
        apply: bool,
        timeout: int,
        environment: dict[str, str] | None = None,
    ) -> VerificationResult:
        self.calls.append(
            {
                "repository": str(snapshot.repository),
                "recipe": step.recipe,
                "apply": apply,
                "timeout": timeout,
                "environment": environment,
            }
        )
        return VerificationResult(
            command=["rewrite", "--dry-run", step.recipe],
            exit_code=0,
            stdout="Preview completed",
            duration_seconds=0.25,
        )


def test_migration_preview_dispatch_and_result_are_characterized(tmp_path: Path) -> None:
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    persisted = PersistedMigrationPlan(
        plan_id="plan1",
        profile="spring-boot-3.2-java-17",
        snapshot=_snapshot(tmp_path),
        steps=[_recommendation()],
    )
    (store.root / "plan1.json").write_text(persisted.model_dump_json())
    preview = _PreviewRunner()
    progress: list[str] = []

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: preview},
        timeout=42,
        progress=progress.append,
    ).run("plan1", apply=False)

    _assert_golden(
        "migration-preview.json",
        _normalized(
            {
                "result": result.model_dump(mode="json"),
                "runner_calls": preview.calls,
                "progress": progress,
            },
            tmp_path,
        ),
    )


def test_profile_convergence_transitions_are_characterized(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project />\n")
    events: list[dict[str, Any]] = []
    reports = iter(
        [
            RunReport(
                run_id="plan-1",
                profile="spring-boot-3.2-java-17",
                status=RunStatus.PLANNED,
                snapshot=_snapshot(tmp_path),
                context_graph=ContextGraphInfo(graph_path=tmp_path / "graph.json"),
                plan=RecipePlan(recommendations=[_recommendation()]),
            ),
            RunReport(
                run_id="plan-2",
                profile="spring-boot-3.2-java-17",
                status=RunStatus.PLANNED,
                snapshot=_snapshot(tmp_path),
                context_graph=ContextGraphInfo(graph_path=tmp_path / "graph.json"),
                plan=RecipePlan(),
            ),
        ]
    )

    def analyze(run_sonar: bool) -> bool:
        events.append({"operation": "analyze", "run_sonar": run_sonar})
        return True

    def plan() -> RunReport:
        value = next(reports)
        events.append({"operation": "plan", "plan_id": value.run_id})
        return value

    def migrate(plan_id: str, checkpoint: Any) -> MigrationRun:
        events.append(
            {
                "operation": "migrate",
                "plan_id": plan_id,
                "checkpoint": checkpoint,
            }
        )
        return MigrationRun(
            plan_id=plan_id,
            status=RunStatus.COMPLETED,
            published_revision="revision-1",
            verified_java=17,
        )

    runner = ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=analyze,
        plan=plan,
        migrate=migrate,
        progress=lambda message: events.append({"operation": "progress", "message": message}),
    )
    result = runner.run()

    _assert_golden(
        "profile-convergence.json",
        _normalized(
            {
                "result": result.model_dump(mode="json"),
                "events": events,
                "persisted_state": json.loads(runner.store.path.read_text()),
            },
            tmp_path,
        ),
    )
