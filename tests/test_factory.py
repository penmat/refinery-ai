import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from typer.testing import CliRunner

from refinery_ai.agents.planning import create_planning_agent, grounded_recipe_plan
from refinery_ai.cli import app
from refinery_ai.config import Settings
from refinery_ai.context_graph.models import ContextGraphInfo
from refinery_ai.factory import (
    ModernizationFactory,
    _planning_evidence_facts,
    _retryable_repaired_recipes,
    _with_boot3_before_jakarta,
    _with_boot3_before_springdoc2,
    _with_datasource_initialization_prerequisite,
    _with_utf8_prerequisite,
)
from refinery_ai.models import (
    DiscoverySnapshot,
    ModernizationGoal,
    RecipePlan,
    RecipeRecommendation,
    RunStatus,
)
from refinery_ai.planning_artifacts import load_migration_failures, load_planner_rules
from refinery_ai.recipe_catalog import JavaMigrationCatalog, RecipeCatalogEntry
from refinery_ai.recipe_sources import RecipeSource


class FakeContextGraph:
    def build(self, repository: Path) -> ContextGraphInfo:
        output = repository / ".refinery/fake-context"
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


def test_planner_rules_capture_spring_boot_java_17_ordering() -> None:
    rules = load_planner_rules()

    assert "title: Upgrade Spring Boot 2.x before Java 8 or 11 to Java 17" in rules
    assert "tags: [refinery, migration, planning, java, spring-boot]" in rules
    assert "any Spring Boot release older than 2.7" in rules
    assert "upgrade Spring\nBoot to 2.7.x" in rules
    assert "Verify the Spring Boot 2.7 upgrade before" in rules
    assert "UpgradeToJava17` depend" in rules
    assert "class-file version 61" in rules
    assert "Normalize application properties to UTF-8 before Spring Boot upgrades" in rules
    assert "Do not\nschedule the Spring Boot upgrade" in rules


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


def test_planner_filters_recipes_that_failed_in_this_repository() -> None:
    recommendation = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.UpgradeToJava17",
        reason="The project targets Java 8.",
        evidence=['"java_version": "8"'],
        expected_benefit="Upgrade the Java baseline.",
        priority="high",
        confidence=0.98,
    )

    result = grounded_recipe_plan(
        RecipePlan(recommendations=[recommendation]),
        frozenset({recommendation.recipe}),
        'Discovery: {"java_version": "8"}',
        failed_recipes=frozenset({recommendation.recipe}),
    )

    assert result.recommendations == []


def test_planner_filters_recipes_that_completed_in_this_repository() -> None:
    recommendation = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.SpringBoot2JUnit4to5Migration",
        reason="The project still contains JUnit 4 tests.",
        evidence=["org.junit.runner.RunWith"],
        expected_benefit="Move tests to JUnit Jupiter.",
        priority="medium",
        confidence=0.9,
    )

    result = grounded_recipe_plan(
        RecipePlan(recommendations=[recommendation]),
        frozenset({recommendation.recipe}),
        "org.junit.runner.RunWith",
        completed_recipes=frozenset({recommendation.recipe}),
    )

    assert result.recommendations == []


def test_load_migration_failures_returns_structured_planning_evidence(
    tmp_path: Path,
) -> None:
    failed_step = tmp_path / ".refinery/migration-runs/run-1/step-002"
    failed_step.mkdir(parents=True)
    failed_step.joinpath("step.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "reason": "Build verification failed",
                "base_revision": "abc123",
                "recipe": {
                    "recipe": "org.openrewrite.java.springdoc.SpringFoxToSpringDoc"
                },
                "verification": {
                    "exit_code": 1,
                    "stderr": "cannot find symbol: Docket",
                },
            }
        )
    )
    completed_step = tmp_path / ".refinery/migration-runs/run-2/step-001"
    completed_step.mkdir(parents=True)
    completed_step.joinpath("step.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "recipe": {
                    "recipe": (
                        "org.openrewrite.java.spring.boot2."
                        "SpringBoot2JUnit4to5Migration"
                    )
                },
            }
        )
    )

    history = load_migration_failures(tmp_path, Path(".refinery"), 10_000)

    assert history.failed_recipes == frozenset(
        {"org.openrewrite.java.springdoc.SpringFoxToSpringDoc"}
    )
    assert history.completed_recipes == frozenset(
        {"org.openrewrite.java.spring.boot2.SpringBoot2JUnit4to5Migration"}
    )
    assert "Build verification failed" in history.context
    assert "cannot find symbol: Docket" in history.context
    assert ".refinery/migration-runs/run-1/step-002" in history.context


def test_planning_evidence_facts_include_goal_and_retained_warnings(tmp_path: Path) -> None:
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system="maven",
        spring_boot_version="2.1.6.RELEASE",
        non_utf8_application_properties=[Path("src/main/resources/application.properties")],
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
    assert "discovery.spring_boot_version=2.1.6.RELEASE" in facts
    assert (
        "discovery.non_utf8_application_properties="
        "src/main/resources/application.properties"
    ) in facts
    assert "- [warning] Legacy javax imports remain." in facts


def test_utf8_conversion_is_planned_before_spring_boot_upgrade(tmp_path: Path) -> None:
    boot = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
        reason="Upgrade Boot.",
        evidence=["discovery.spring_boot_version=2.1.6.RELEASE"],
        expected_benefit="Boot 2.7 support.",
        priority="high",
        confidence=0.9,
    )
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system="maven",
        non_utf8_application_properties=[Path("src/main/resources/application.properties")],
    )

    plan = _with_utf8_prerequisite(RecipePlan(recommendations=[boot]), snapshot)

    assert plan.recommendations[0].execution_type.value == "utf8"
    assert plan.recommendations[0].recipe == (
        "refinery.convert-application-properties-to-utf8"
    )
    assert plan.recommendations[1].depends_on == [
        "refinery.convert-application-properties-to-utf8"
    ]


def test_datasource_initialization_is_planned_before_boot_upgrade(tmp_path: Path) -> None:
    boot = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
        reason="Upgrade Boot.",
        evidence=["boot"],
        expected_benefit="Boot 2.7.",
        priority="high",
        confidence=1,
    )
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system="maven",
        deferred_datasource_initialization_properties=[
            Path("src/main/resources/application.properties")
        ],
    )

    plan = _with_datasource_initialization_prerequisite(
        RecipePlan(recommendations=[boot]), snapshot
    )

    assert plan.recommendations[0].execution_type.value == "spring-properties"
    assert plan.recommendations[1].depends_on == [
        "refinery.defer-datasource-initialization"
    ]


def test_boot3_is_enforced_before_standalone_jakarta_migration(tmp_path: Path) -> None:
    jakarta_recipe = "org.openrewrite.java.migrate.jakarta.JavaxMigrationToJakarta"
    boot3_recipe = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"
    common = {
        "reason": "Modernize Spring.",
        "evidence": ["spring"],
        "expected_benefit": "Modern Spring stack.",
        "priority": "high",
        "confidence": 1,
    }
    jakarta = RecipeRecommendation(
        recipe=jakarta_recipe,
        depends_on=["boot2"],
        **common,
    )
    boot3 = RecipeRecommendation(
        recipe=boot3_recipe,
        depends_on=[jakarta_recipe, "boot2"],
        **common,
    )

    result = _with_boot3_before_jakarta(
        RecipePlan(recommendations=[jakarta, boot3]),
        DiscoverySnapshot(
            repository=tmp_path,
            build_system="maven",
            spring_boot_version="2.7.18",
        ),
    )

    assert [item.recipe for item in result.recommendations] == [
        boot3_recipe,
        jakarta_recipe,
    ]
    assert result.recommendations[0].depends_on == ["boot2"]
    assert result.recommendations[1].depends_on == [boot3_recipe, "boot2"]


def test_boot3_is_enforced_before_springdoc2_migration(tmp_path: Path) -> None:
    springdoc_recipe = "org.openrewrite.java.springdoc.UpgradeSpringDoc_2"
    boot3_recipe = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"
    common = {
        "reason": "Modernize Spring.",
        "evidence": ["spring"],
        "expected_benefit": "Modern Spring stack.",
        "priority": "high",
        "confidence": 1,
    }
    springdoc = RecipeRecommendation(
        recipe=springdoc_recipe,
        depends_on=["boot2"],
        **common,
    )
    boot3 = RecipeRecommendation(
        recipe=boot3_recipe,
        depends_on=[springdoc_recipe, "boot2"],
        **common,
    )

    result = _with_boot3_before_springdoc2(
        RecipePlan(recommendations=[springdoc, boot3]),
        DiscoverySnapshot(
            repository=tmp_path,
            build_system="maven",
            spring_boot_version="2.7.18",
        ),
    )

    assert [item.recipe for item in result.recommendations] == [
        boot3_recipe,
        springdoc_recipe,
    ]
    assert result.recommendations[0].depends_on == ["boot2"]
    assert result.recommendations[1].depends_on == [boot3_recipe, "boot2"]


def test_springdoc2_failure_is_retryable_after_ordering_repair_is_available(
    tmp_path: Path,
) -> None:
    retryable = _retryable_repaired_recipes(
        tmp_path,
        "NoClassDefFoundError: org/springframework/http/HttpStatusCode",
    )

    assert retryable == {"org.openrewrite.java.springdoc.UpgradeSpringDoc_2"}


def test_boot3_failure_is_retryable_after_generator_repair_is_available(
    tmp_path: Path,
) -> None:
    (tmp_path / "pom.xml").write_text(
        """<project>
  <openapi-generator-maven-plugin.version>5.2.1</openapi-generator-maven-plugin.version>
  <artifactId>openapi-generator-maven-plugin</artifactId>
</project>
"""
    )

    retryable = _retryable_repaired_recipes(
        tmp_path,
        "package javax.validation does not exist",
    )

    assert retryable == {
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"
    }


def test_boot3_failure_is_retryable_after_test_compatibility_repair_is_available(
    tmp_path: Path,
) -> None:
    retryable = _retryable_repaired_recipes(
        tmp_path,
        "shouldDeletePet:213 » InvalidDataAccessApiUsage",
    )

    assert retryable == {
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"
    }


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
    assert (tmp_path / ".refinery/fake-context/graph.json").exists()
    assert (tmp_path / f".refinery/runs/{report.run_id}.json").exists()
    assert (tmp_path / f".refinery/runs/{report.run_id}.md").exists()


def test_ai_prepare_persists_migration_plan_handoff(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    recommendation = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.UpgradeToJava17",
        reason="Upgrade the Java baseline.",
        evidence=["goal.target_java=17"],
        expected_benefit="Java 17 support.",
        priority="high",
        confidence=0.9,
    )
    planner = FakePlanner()
    planner.run_sync = lambda prompt, *, deps: SimpleNamespace(  # type: ignore[method-assign]
        output=RecipePlan(recommendations=[recommendation])
    )
    monkeypatch.setattr(
        "refinery_ai.factory.create_planning_agent", lambda model, **kwargs: planner
    )

    report = ModernizationFactory(
        context_graph=FakeContextGraph(), recipe_catalog=CATALOG, recipe_sources=RECIPE_SOURCES
    ).prepare(
        tmp_path,
        ModernizationGoal(description="Upgrade Java", target_java=17),
        use_ai_planner=True,
    )

    persisted = json.loads((tmp_path / f".refinery/plans/{report.run_id}.json").read_text())
    assert persisted["plan_id"] == report.run_id
    assert persisted["steps"] == [recommendation.model_dump(mode="json")]


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
    assert "any Spring Boot release older than 2.7" in planner.deps.planner_rules
    assert "JavaxMigrationToJakarta" in planner.deps.planner_rules
    assert "Spring Boot 3 or greater" in planner.deps.planner_rules
    assert "org.openrewrite.java.migrate.UpgradeToJava17" in planner.deps.recipe_catalog
    assert planner.deps.allowed_recipes == {
        "org.openrewrite.java.migrate.UpgradeToJava17"
    }
    assert '"target_java": 17' in planner.deps.evidence_corpus
    assert "goal.target_java=17" in planner.deps.evidence_facts
    assert "No persisted project artifacts are available." in planner.deps.evidence_corpus
    assert planner.deps.migration_failure_context == (
        "No previous migration failures are recorded."
    )
    assert planner.deps.failed_recipes == frozenset()
    assert planner.deps.completed_recipes == frozenset()


def test_ai_prepare_supplies_previous_migration_failures(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    failed_step = tmp_path / ".refinery/migration-runs/run-1/step-001"
    failed_step.mkdir(parents=True)
    failed_step.joinpath("step.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "reason": "Build verification failed",
                "recipe": {
                    "recipe": "org.openrewrite.java.migrate.UpgradeToJava17"
                },
                "verification": {"exit_code": 1, "stdout": "compilation failed"},
            }
        )
    )
    planner = FakePlanner()
    monkeypatch.setattr(
        "refinery_ai.factory.create_planning_agent", lambda model, **kwargs: planner
    )

    ModernizationFactory(
        context_graph=FakeContextGraph(),
        recipe_catalog=CATALOG,
        recipe_sources=RECIPE_SOURCES,
    ).prepare(
        tmp_path,
        ModernizationGoal(description="Upgrade Java", target_java=17),
        use_ai_planner=True,
    )

    assert planner.deps is not None
    assert planner.deps.failed_recipes == frozenset(
        {"org.openrewrite.java.migrate.UpgradeToJava17"}
    )
    assert "compilation failed" in planner.deps.migration_failure_context
    assert "compilation failed" in planner.deps.evidence_corpus


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
        profile: object | None = None,
    ) -> SimpleNamespace:
        assert profile is None
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
        profile: object | None = None,
    ) -> SimpleNamespace:
        assert profile is None
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
