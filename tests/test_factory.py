import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from pydantic_ai.models.openai import OpenAIResponsesModel
from typer.testing import CliRunner

from refinery_ai.agents.contracts import Planner, PlanningRequest
from refinery_ai.agents.planning import create_planning_agent, grounded_recipe_plan
from refinery_ai.cli import app
from refinery_ai.config import Settings
from refinery_ai.context_graph.models import ContextGraphInfo
from refinery_ai.factory import (
    ModernizationFactory,
    _planning_evidence_facts,
    _retryable_repaired_recipes,
    _with_boot2_before_validation_starter,
    _with_boot3_before_jakarta,
    _with_boot3_before_mybatis3,
    _with_boot3_before_springdoc2,
    _with_datasource_initialization_prerequisite,
    _with_springfox_before_boot27,
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
from refinery_ai.recipes.sources import RecipeSource


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
    def __init__(self, output: RecipePlan | None = None) -> None:
        self.deps = None
        self.request: PlanningRequest | None = None
        self.output = output or RecipePlan()

    def plan(self, request: PlanningRequest) -> RecipePlan:
        self.request = request
        self.deps = request.dependencies
        return self.output


RECIPE_SOURCES = [
    RecipeSource(
        recipe="org.openrewrite.java.migrate.UpgradeToJava17",
        display_name="Migrate to Java 17",
        description="Apply changes commonly needed when migrating to Java 17.",
        repository="openrewrite/rewrite-migrate-java",
        file_path="src/main/resources/META-INF/rewrite/java-version-17.yml",
    )
]


def test_planner_and_repair_models_are_explicitly_configured() -> None:
    settings = Settings()

    assert settings.planner_model == "openai:gpt-5.6-luna"
    assert settings.repair_model == "openai:gpt-5.6-luna"


def test_deterministic_and_ai_repairs_each_get_three_attempts() -> None:
    settings = Settings()

    assert settings.max_deterministic_repair_attempts == 3
    assert settings.max_repair_attempts == 3


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
    assert "Add the validation starter after the Spring Boot bridge" in rules
    assert "when it makes no source\nor build-file changes" in rules
    assert "Convert Springfox before upgrading to Spring Boot 2.7" in rules


def test_planner_uses_the_configured_ollama_api_base() -> None:
    planner = create_planning_agent(
        "ollama:gemma4:latest", ollama_base_url="http://ollama.test:11434/"
    )

    assert str(cast(Any, planner.model).base_url) == "http://ollama.test:11434/v1/"


def test_openai_planner_uses_the_responses_api_for_reasoning_tools() -> None:
    planner = create_planning_agent(
        "openai:gpt-5.6-luna",
        openai_api_key="test-key",
    )

    assert isinstance(planner.model, OpenAIResponsesModel)


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
                "recipe": {"recipe": "org.openrewrite.java.springdoc.SpringFoxToSpringDoc"},
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
                    "recipe": ("org.openrewrite.java.spring.boot2.SpringBoot2JUnit4to5Migration")
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


def test_load_migration_failures_keeps_stdout_when_stderr_is_nonempty(
    tmp_path: Path,
) -> None:
    failed_step = tmp_path / ".refinery/migration-runs/run-1/step-001"
    failed_step.mkdir(parents=True)
    failed_step.joinpath("step.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "reason": "Post-migration build failed",
                "recipe": {"recipe": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"},
                "verification": {
                    "exit_code": 1,
                    "stdout": "EnableGlobalMethodSecurity is required",
                    "stderr": "Conditions evaluation report",
                },
            }
        )
    )

    history = load_migration_failures(tmp_path, Path(".refinery"), 10_000)

    assert "EnableGlobalMethodSecurity is required" in history.context
    assert "Conditions evaluation report" in history.context


def test_no_change_migration_is_not_recorded_as_completed(tmp_path: Path) -> None:
    step = tmp_path / ".refinery/migration-runs/run-1/step-001"
    step.mkdir(parents=True)
    step.joinpath("step.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "recipe": {
                    "recipe": ("org.openrewrite.java.spring.boot2.AddSpringBootStarterValidation")
                },
                "base_revision": "abc123",
                "checkpoint": "abc123",
            }
        )
    )

    history = load_migration_failures(tmp_path, Path(".refinery"), 10_000)

    assert history.completed_recipes == frozenset()


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
        "discovery.non_utf8_application_properties=src/main/resources/application.properties"
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
    assert plan.recommendations[0].recipe == ("refinery.convert-application-properties-to-utf8")
    assert plan.recommendations[1].depends_on == ["refinery.convert-application-properties-to-utf8"]


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
    assert plan.recommendations[1].depends_on == ["refinery.defer-datasource-initialization"]


def test_boot2_upgrade_is_enforced_before_validation_starter() -> None:
    boot_recipe = "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_5"
    validation_recipe = "org.openrewrite.java.spring.boot2.AddSpringBootStarterValidation"
    common = {
        "reason": "Modernize validation.",
        "evidence": ["validation"],
        "expected_benefit": "Compatible validation.",
        "priority": "high",
        "confidence": 1,
    }
    validation = RecipeRecommendation(
        recipe=validation_recipe,
        depends_on=["constraints"],
        **common,
    )
    boot = RecipeRecommendation(
        recipe=boot_recipe,
        depends_on=[validation_recipe, "constraints"],
        **common,
    )

    result = _with_boot2_before_validation_starter(RecipePlan(recommendations=[validation, boot]))

    assert [item.recipe for item in result.recommendations] == [
        boot_recipe,
        validation_recipe,
    ]
    assert result.recommendations[0].depends_on == ["constraints"]
    assert result.recommendations[1].depends_on == [boot_recipe, "constraints"]


def test_springfox_conversion_is_enforced_before_boot_27() -> None:
    springfox_recipe = "org.openrewrite.java.springdoc.SpringFoxToSpringDoc"
    boot27_recipe = "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7"
    common = {
        "reason": "Modernize API documentation.",
        "evidence": ["springfox"],
        "expected_benefit": "Compatible API documentation.",
        "priority": "high",
        "confidence": 1,
    }
    boot27 = RecipeRecommendation(
        recipe=boot27_recipe,
        depends_on=["encoding"],
        **common,
    )
    springfox = RecipeRecommendation(
        recipe=springfox_recipe,
        depends_on=[boot27_recipe],
        **common,
    )

    result = _with_springfox_before_boot27(
        RecipePlan(recommendations=[boot27, springfox]),
        DiscoverySnapshot(repository=Path("."), build_system="maven"),
    )

    assert [item.recipe for item in result.recommendations] == [
        springfox_recipe,
        boot27_recipe,
    ]
    assert result.recommendations[0].depends_on == []
    assert result.recommendations[1].depends_on == [springfox_recipe, "encoding"]


def test_springfox_prerequisite_is_restored_when_failure_history_filtered_it() -> None:
    boot27_recipe = "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7"
    boot27 = RecipeRecommendation(
        recipe=boot27_recipe,
        reason="Upgrade Boot.",
        evidence=["boot"],
        expected_benefit="Boot 2.7.",
        priority="high",
        confidence=1,
    )
    snapshot = DiscoverySnapshot(
        repository=Path("."),
        build_system="maven",
        dependencies=[{"group": "io.springfox", "artifact": "springfox-swagger2"}],
    )

    result = _with_springfox_before_boot27(RecipePlan(recommendations=[boot27]), snapshot)

    assert [item.recipe for item in result.recommendations] == [
        "org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
        boot27_recipe,
    ]
    assert result.recommendations[1].depends_on == [
        "org.openrewrite.java.springdoc.SpringFoxToSpringDoc"
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


def test_mybatis3_is_deferred_until_spring_boot3_is_planned(tmp_path: Path) -> None:
    mybatis_recipe = "org.openrewrite.java.spring.boot3.UpgradeMyBatisToSpringBoot_3_0"
    mybatis = RecipeRecommendation(
        recipe=mybatis_recipe,
        reason="Upgrade MyBatis.",
        evidence=["mybatis"],
        expected_benefit="MyBatis 3.",
        priority="high",
        confidence=1,
    )

    result = _with_boot3_before_mybatis3(
        RecipePlan(recommendations=[mybatis]),
        DiscoverySnapshot(
            repository=tmp_path,
            build_system="maven",
            spring_boot_version="2.7.5",
        ),
    )

    assert result.recommendations == []


def test_mybatis3_failure_is_retryable_after_ordering_repair() -> None:
    retryable = _retryable_repaired_recipes(
        Path("."),
        "NoClassDefFoundError: org/springframework/aot/AotDetector",
    )

    assert retryable == {"org.openrewrite.java.spring.boot3.UpgradeMyBatisToSpringBoot_3_0"}


def test_boot_failures_are_retryable_after_h2_and_fastjson_repairs() -> None:
    retryable = _retryable_repaired_recipes(
        Path("."),
        'NULL not allowed for column "COMMENT_ID"\n'
        "ObjectReaderProvider cannot be converted to ParserConfig",
    )

    assert retryable == {
        "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_0",
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
    }


def test_springdoc2_failure_is_retryable_after_ordering_repair_is_available(
    tmp_path: Path,
) -> None:
    retryable = _retryable_repaired_recipes(
        tmp_path,
        "NoClassDefFoundError: org/springframework/http/HttpStatusCode",
    )

    assert retryable == {"org.openrewrite.java.springdoc.UpgradeSpringDoc_2"}


def test_boot27_springfox_failure_is_retryable_after_ordering_rule() -> None:
    retryable = _retryable_repaired_recipes(
        Path("."),
        "package springfox.documentation.swagger2.annotations does not exist",
    )

    assert retryable == {
        "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
        "org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
    }


def test_springfox_generated_annotation_failure_is_retryable_after_repair() -> None:
    retryable = _retryable_repaired_recipes(
        Path("."),
        "[ERROR] symbol:   class ApiModelProperty",
    )

    assert retryable == {"org.openrewrite.java.springdoc.SpringFoxToSpringDoc"}


def test_springfox_schema_version_failure_is_retryable_after_repair() -> None:
    retryable = _retryable_repaired_recipes(
        Path("."),
        (
            "symbol:   method requiredMode()\n"
            "location: @interface io.swagger.v3.oas.annotations.media.Schema"
        ),
    )

    assert retryable == {"org.openrewrite.java.springdoc.SpringFoxToSpringDoc"}


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

    assert retryable == {"org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"}


def test_boot3_failure_is_retryable_after_test_compatibility_repair_is_available(
    tmp_path: Path,
) -> None:
    retryable = _retryable_repaired_recipes(
        tmp_path,
        "shouldDeletePet:213 » InvalidDataAccessApiUsage",
    )

    assert retryable == {"org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"}


def test_boot2_validation_failure_is_retryable_after_dependency_repair() -> None:
    retryable = _retryable_repaired_recipes(
        Path("."),
        "package org.hibernate.validator.constraints does not exist",
    )

    assert retryable == {
        f"org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_{minor}" for minor in range(3, 8)
    }


def test_boot26_circular_security_failure_is_retryable_after_repair() -> None:
    retryable = _retryable_repaired_recipes(
        Path("."),
        "BeanCurrentlyInCreationException: webSecurityConfig",
    )

    assert retryable == {"org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_6"}


def test_boot27_repository_rest_override_failure_is_retryable(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/example/MyDataRestConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class MyDataRestConfig implements RepositoryRestConfigurer {\n"
        "  void configureRepositoryRestConfiguration(RepositoryRestConfiguration config) {}\n"
        "}\n"
    )

    retryable = _retryable_repaired_recipes(
        tmp_path,
        "method does not override or implement a method from a supertype",
    )

    assert retryable == {"org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7"}


def test_prepare_persists_reports(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    report = ModernizationFactory(
        context_graph=FakeContextGraph(),
        recipe_sources=RECIPE_SOURCES,
    ).prepare(tmp_path, ModernizationGoal(description="Upgrade Java", target_java=17))
    assert report.status is RunStatus.PLANNED
    assert report.context_graph.provider == "context-graph"
    assert (tmp_path / ".refinery/fake-context/graph.json").exists()
    assert (tmp_path / f".refinery/runs/{report.run_id}.json").exists()
    assert (tmp_path / f".refinery/runs/{report.run_id}.md").exists()


def test_ai_prepare_persists_migration_plan_handoff(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    recommendation = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.UpgradeToJava17",
        reason="Upgrade the Java baseline.",
        evidence=["goal.target_java=17"],
        expected_benefit="Java 17 support.",
        priority="high",
        confidence=0.9,
    )
    planner = FakePlanner(RecipePlan(recommendations=[recommendation]))

    report = ModernizationFactory(
        context_graph=FakeContextGraph(), recipe_sources=RECIPE_SOURCES, planner=planner
    ).prepare(
        tmp_path,
        ModernizationGoal(description="Upgrade Java", target_java=17),
        use_ai_planner=True,
    )

    persisted = json.loads((tmp_path / f".refinery/plans/{report.run_id}.json").read_text())
    assert persisted["plan_id"] == report.run_id
    assert persisted["steps"] == [recommendation.model_dump(mode="json")]


def test_ai_prepare_supplies_graph_and_assessment_context(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    planner = FakePlanner()

    report = ModernizationFactory(
        context_graph=FakeContextGraph(),
        recipe_sources=RECIPE_SOURCES,
        planner=planner,
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
    assert planner.deps.recipe_catalog.recipe_count == 1
    assert planner.deps.recipe_catalog.allowed_recipes == {
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
    assert planner.request is not None
    assert planner.request.run_id == report.run_id
    assert planner.request.plan_id == report.run_id
    assert "Recommend applicable Java migration recipes" in planner.request.prompt


def test_ai_prepare_supplies_previous_migration_failures(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    failed_step = tmp_path / ".refinery/migration-runs/run-1/step-001"
    failed_step.mkdir(parents=True)
    failed_step.joinpath("step.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "reason": "Build verification failed",
                "recipe": {"recipe": "org.openrewrite.java.migrate.UpgradeToJava17"},
                "verification": {"exit_code": 1, "stdout": "compilation failed"},
            }
        )
    )
    planner = FakePlanner()

    ModernizationFactory(
        context_graph=FakeContextGraph(),
        recipe_sources=RECIPE_SOURCES,
        planner=planner,
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


def test_fake_planner_satisfies_framework_neutral_contract() -> None:
    assert isinstance(FakePlanner(), Planner)


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
        refresh_recipe_catalog: bool = False,
    ) -> SimpleNamespace:
        assert profile is None
        assert refresh_recipe_catalog is True
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
            "--refresh-recipe-catalog",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "recommendations": [recommendation.model_dump(mode="json")]
    }
    assert calls == [
        (tmp_path, ModernizationGoal(description="Upgrade Java", target_java=21), True)
    ]


def test_run_command_exposes_repair_mode_override() -> None:
    result = CliRunner().invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    assert "--repair-mode" in result.stdout
    assert "Re-download" in result.stdout


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
        refresh_recipe_catalog: bool = False,
    ) -> SimpleNamespace:
        assert profile is None
        assert refresh_recipe_catalog is False
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
