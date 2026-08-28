import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from typer.testing import CliRunner

from refinery_ai.agents.planning import create_planning_agent, grounded_recipe_plan
from refinery_ai.cli import app
from refinery_ai.config import Settings
from refinery_ai.context_graph.models import ContextGraphInfo
from refinery_ai.factory import ModernizationFactory, _planning_evidence_facts
from refinery_ai.models import (
    DiscoverySnapshot,
    ModernizationGoal,
    RecipePlan,
    RecipeRecommendation,
    RunStatus,
)
from refinery_ai.recipe_catalog import JavaMigrationCatalog, RecipeCatalogEntry
from refinery_ai.recipe_sources import RecipeSource


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
RECIPE_SOURCES = [
    RecipeSource(
        recipe="org.openrewrite.java.migrate.UpgradeToJava17",
        display_name="Migrate to Java 17",
        description="Apply changes commonly needed when migrating to Java 17.",
        repository="openrewrite/rewrite-migrate-java",
        file_path="src/main/resources/META-INF/rewrite/java-version-17.yml",
    )
]


def test_planner_defaults_to_openai_terra() -> None:
    assert Settings().model == "openai:gpt-5.6-terra"


def test_planner_uses_the_configured_ollama_api_base() -> None:
    planner = create_planning_agent(
        "ollama:gemma4:latest", ollama_base_url="http://ollama.test:11434/"
    )

    assert str(cast(Any, planner.model).base_url) == "http://ollama.test:11434/v1/"


def test_planner_filters_ungrounded_recommendations_instead_of_failing() -> None:
    valid = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.UpgradeToJava17",
        reason="The project targets Java 8.",
        evidence=['"java_version": "8"'],
        expected_benefit="Upgrade the Java baseline.",
        priority="high",
        confidence=0.98,
    )
    catalog_evidence = valid.model_copy(
        update={
            "recipe": "org.openrewrite.java.migrate.guava.PreferCharCompare",
            "evidence": ["File: src/main/resources/META-INF/rewrite/no-guava.yml"],
        }
    )
    unknown = valid.model_copy(update={"recipe": "org.example.NotARecipe"})

    result = grounded_recipe_plan(
        RecipePlan(recommendations=[valid, catalog_evidence, unknown]),
        frozenset(
            {
                valid.recipe,
                "org.openrewrite.java.migrate.guava.PreferCharCompare",
            }
        ),
        'Discovery: {"java_version": "8"}',
    )

    assert result.recommendations == [valid]


def test_planning_evidence_facts_include_goal_and_retained_warnings(tmp_path: Path) -> None:
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system="maven",
        frameworks=["Spring"],
    )

    facts = _planning_evidence_facts(
        snapshot,
        ModernizationGoal(description="Upgrade Java", target_java=17),
        "# Summary\n- [warning] Legacy javax imports remain.\n",
    )

    assert "discovery.java_version=unknown" in facts
    assert "goal.target_java=17" in facts
    assert "discovery.framework=Spring" in facts
    assert "- [warning] Legacy javax imports remain." in facts


def test_prepare_persists_reports(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    report = ModernizationFactory(
        context_graph=FakeContextGraph(),
        recipe_catalog=CATALOG,
        recipe_sources=RECIPE_SOURCES,
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
        context_graph=FakeContextGraph(),
        recipe_catalog=CATALOG,
        recipe_sources=RECIPE_SOURCES,
    ).prepare(
        tmp_path,
        ModernizationGoal(description="Upgrade Java", target_java=17),
        use_ai_planner=True,
    )

    assert report.status is RunStatus.PLANNED
    assert planner.deps is not None
    assert "Context graph with 0 nodes" in planner.deps.graph_summary
    assert '"question": "Upgrade Java Java version source target' in planner.deps.graph_context
    assert planner.deps.artifact_context == "No persisted project artifacts are available."
    assert "org.openrewrite.java.migrate.UpgradeToJava17" in planner.deps.recipe_catalog
    assert planner.deps.allowed_recipes == {
        "org.openrewrite.java.migrate.UpgradeToJava17"
    }
    assert '"target_java": 17' in planner.deps.evidence_corpus
    assert "goal.target_java=17" in planner.deps.evidence_facts
    assert "No persisted project artifacts are available." in planner.deps.evidence_corpus


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


def test_plan_command_accepts_repository_as_the_positional_argument(
    tmp_path: Path, monkeypatch: Any
) -> None:
    calls: list[tuple[Path, ModernizationGoal, bool]] = []

    def fake_prepare(
        self: ModernizationFactory,
        repository: Path,
        goal: ModernizationGoal,
        *,
        use_ai_planner: bool = False,
    ) -> SimpleNamespace:
        calls.append((repository, goal, use_ai_planner))
        return SimpleNamespace(plan=RecipePlan())

    monkeypatch.setattr(ModernizationFactory, "prepare", fake_prepare)
    result = CliRunner().invoke(app, ["plan", str(tmp_path)])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"recommendations": []}
    assert calls == [
        (
            tmp_path,
            ModernizationGoal(description="Recommend applicable Java migration recipes"),
            True,
        )
    ]
