import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from typer.testing import CliRunner

from refinery_ai.agents import create_planning_agent
from refinery_ai.cli import app
from refinery_ai.config import Settings
from refinery_ai.context_graph.models import ContextGraphInfo
from refinery_ai.factory import ModernizationFactory
from refinery_ai.models import ModernizationGoal, RecipePlan, RecipeRecommendation, RunStatus
from refinery_ai.recipe_catalog import JavaMigrationCatalog, RecipeCatalogEntry


class FakeContextGraph:
    def build(self, repository: Path) -> ContextGraphInfo:
        output = repository / "graphify-out"
        output.mkdir(exist_ok=True)
        graph = output / "graph.json"
        graph.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "repository": str(repository),
                    "nodes": [],
                    "edges": [],
                    "provider_runs": [],
                }
            )
        )
        return ContextGraphInfo(graph_path=graph)


class FakePlanner:
    def __init__(self) -> None:
        self.deps = None

    def run_sync(self, prompt: str, *, deps: object) -> SimpleNamespace:
        self.deps = deps
        return SimpleNamespace(output=RecipePlan())


CATALOG = JavaMigrationCatalog(
    recipes=[
        RecipeCatalogEntry(
            name="Migrate to Java 17",
            url="https://docs.openrewrite.org/recipes/java/migrate/upgradetojava17",
            kind="Composite Recipes",
        )
    ]
)


def test_planner_defaults_to_the_local_ollama_model() -> None:
    assert Settings().model == "ollama:gemma4:latest"


def test_planner_uses_the_configured_ollama_api_base() -> None:
    planner = create_planning_agent(
        "ollama:gemma4:latest", ollama_base_url="http://ollama.test:11434/"
    )

    assert str(cast(Any, planner.model).base_url) == "http://ollama.test:11434/v1/"


def test_prepare_persists_reports(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    report = ModernizationFactory(
        context_graph=FakeContextGraph(), recipe_catalog=CATALOG
    ).prepare(
        tmp_path, ModernizationGoal(description="Upgrade Java", target_java=17)
    )
    assert report.status is RunStatus.PLANNED
    assert report.context_graph.provider == "context-graph"
    assert (tmp_path / "graphify-out/graph.json").exists()
    assert (tmp_path / f".refinery/runs/{report.run_id}.json").exists()
    assert (tmp_path / f".refinery/runs/{report.run_id}.md").exists()


def test_ai_prepare_supplies_graph_and_assessment_context(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    planner = FakePlanner()
    monkeypatch.setattr(
        "refinery_ai.factory.create_planning_agent",
        lambda model, **kwargs: planner,
    )

    report = ModernizationFactory(
        context_graph=FakeContextGraph(), recipe_catalog=CATALOG
    ).prepare(
        tmp_path,
        ModernizationGoal(description="Upgrade Java", target_java=17),
        use_ai_planner=True,
    )

    assert report.status is RunStatus.PLANNED
    assert planner.deps is not None
    assert "Context graph with 0 nodes" in planner.deps.graph_summary
    assert '"question": "Upgrade Java Spring HTTP endpoint' in planner.deps.graph_context
    assert planner.deps.artifact_context == "No persisted project artifacts are available."
    assert "Migrate to Java 17" in planner.deps.recipe_catalog


def test_apply_stops_on_unknown_build_system(tmp_path: Path) -> None:
    report = ModernizationFactory(context_graph=FakeContextGraph()).run(
        tmp_path, ModernizationGoal(description="Upgrade Java"), apply=True
    )
    assert report.status is RunStatus.FAILED
    assert report.steps[0].message == "Unknown build system"


def test_plan_command_runs_ai_planner_and_prints_its_output(
    tmp_path: Path, monkeypatch: Any
) -> None:
    recommendation = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.UpgradeToJava21",
        reason="The project targets Java 11.",
        evidence=["pom.xml maven.compiler.release=11"],
        expected_benefit="Moves the project to Java 21.",
        priority="high",
        confidence=0.98,
    )
    calls: list[tuple[Path, ModernizationGoal, bool]] = []

    def fake_prepare(
        self: ModernizationFactory,
        repository: Path,
        goal: ModernizationGoal,
        *,
        use_ai_planner: bool = False,
    ) -> SimpleNamespace:
        calls.append((repository, goal, use_ai_planner))
        return SimpleNamespace(plan=RecipePlan(recommendations=[recommendation]))

    monkeypatch.setattr(ModernizationFactory, "prepare", fake_prepare)
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "Upgrade Java",
            "--repository",
            str(tmp_path),
            "--target-java",
            "21",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "recommendations": [recommendation.model_dump(mode="json")]
    }
    assert calls == [
        (tmp_path, ModernizationGoal(description="Upgrade Java", target_java=21), True)
    ]
