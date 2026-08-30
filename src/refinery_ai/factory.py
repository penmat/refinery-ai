"""Modernization factory orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from refinery_ai.agents.planning import PlanningDependencies, create_planning_agent
from refinery_ai.config import Settings
from refinery_ai.context_graph.contracts import ContextGraphBuildService
from refinery_ai.harness import AssessmentHarnessPolicy, HarnessRepositoryEnvironment
from refinery_ai.migrations.spring_properties import DEFER_DATASOURCE_INITIALIZATION_RECIPE
from refinery_ai.migrations.utf8 import UTF8_APPLICATION_PROPERTIES_RECIPE
from refinery_ai.models import (
    DiscoverySnapshot,
    ExecutionType,
    ModernizationGoal,
    ModernizationPlan,
    RecipePlan,
    RecipePriority,
    RecipeRecommendation,
    RunReport,
    RunStatus,
    StepResult,
    StepStrategy,
    VerificationResult,
)
from refinery_ai.openrewrite import OpenRewriteAdapter
from refinery_ai.planning import rule_based_plan, validate_plan
from refinery_ai.planning_artifacts import (
    MigrationPlanStore,
    load_migration_failures,
    load_planner_rules,
    load_planning_artifacts,
)
from refinery_ai.recipe_catalog import JavaMigrationCatalog, fetch_java_migration_catalog
from refinery_ai.recipe_sources import RecipeSource, fetch_recipe_sources
from refinery_ai.reporting import to_markdown
from refinery_ai.repository import RepositoryEnvironment


def _planning_evidence_facts(
    snapshot: DiscoverySnapshot, goal: ModernizationGoal, artifact_context: str
) -> list[str]:
    """Create stable project facts that the planner can quote exactly."""

    facts = [
        f"discovery.build_system={snapshot.build_system.value}",
        f"discovery.java_version={snapshot.java_version or 'unknown'}",
    ]
    if goal.target_java is not None:
        facts.append(f"goal.target_java={goal.target_java}")
    if snapshot.spring_boot_version is not None:
        facts.append(f"discovery.spring_boot_version={snapshot.spring_boot_version}")
    facts.extend(
        f"discovery.non_utf8_application_properties={path}"
        for path in snapshot.non_utf8_application_properties
    )
    facts.extend(
        f"discovery.deferred_datasource_initialization_properties={path}"
        for path in snapshot.deferred_datasource_initialization_properties
    )
    facts.extend(f"discovery.framework={framework}" for framework in snapshot.frameworks)
    facts.extend(
        "discovery.dependency="
        f"{dependency.group}:{dependency.artifact}:{dependency.version or 'unspecified'}"
        for dependency in snapshot.dependencies
    )
    noteworthy_prefixes = (
        "Java:",
        "Build:",
        "Frameworks:",
        "- [warning]",
        "- [error]",
        "Resolved dependencies:",
        "Vulnerable dependencies:",
        "OSV findings:",
    )
    facts.extend(
        line.strip()
        for line in artifact_context.splitlines()
        if line.strip().startswith(noteworthy_prefixes)
    )
    return list(dict.fromkeys(facts))


class ModernizationFactory:
    """Own all control flow, effects, validation, and safety limits."""

    def __init__(
        self,
        settings: Settings | None = None,
        rewrite: OpenRewriteAdapter | None = None,
        context_graph: ContextGraphBuildService | None = None,
        environment: RepositoryEnvironment | None = None,
        recipe_catalog: JavaMigrationCatalog | None = None,
        recipe_sources: list[RecipeSource] | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.environment = environment
        self.context_graph = context_graph
        self.rewrite = rewrite
        self.recipe_catalog = recipe_catalog
        self.recipe_sources = recipe_sources

    def _environment(self, repository: Path) -> RepositoryEnvironment:
        if self.environment is not None:
            return self.environment
        return HarnessRepositoryEnvironment(
            repository,
            policy=AssessmentHarnessPolicy(
                command_timeout_seconds=self.settings.command_timeout_seconds,
                tool_output_limit_characters=self.settings.max_output_characters,
            ),
            context_graph=self.context_graph,
            rewrite=self.rewrite,
            graphify_timeout=self.settings.graphify_timeout_seconds,
            output_limit=self.settings.max_output_characters,
        )

    def prepare(
        self, repository: Path, goal: ModernizationGoal, *, use_ai_planner: bool = False
    ) -> RunReport:
        environment = self._environment(repository)
        snapshot = environment.discover(repository)
        state_dir = snapshot.repository / self.settings.state_directory
        state_dir.mkdir(parents=True, exist_ok=True)
        graph = environment.build_context(snapshot.repository)
        plan: ModernizationPlan | RecipePlan
        if use_ai_planner:
            artifact_context = load_planning_artifacts(
                snapshot.repository,
                self.settings.state_directory,
                self.settings.max_output_characters,
            )
            migration_failures = load_migration_failures(
                snapshot.repository,
                self.settings.state_directory,
                self.settings.max_output_characters,
            )
            failed_recipes = migration_failures.failed_recipes
            if (
                snapshot.non_utf8_application_properties
                or snapshot.deferred_datasource_initialization_properties
            ):
                failed_recipes = frozenset(
                    recipe
                    for recipe in failed_recipes
                    if "UpgradeSpringBoot" not in recipe
                )
            planner_rules = load_planner_rules()
            catalog = self.recipe_catalog or fetch_java_migration_catalog(
                timeout=self.settings.recipe_catalog_timeout_seconds,
                verify_ssl=self.settings.recipe_catalog_verify_ssl,
            )
            recipe_sources = (
                self.recipe_sources
                if self.recipe_sources is not None
                else fetch_recipe_sources(
                    timeout=self.settings.recipe_catalog_timeout_seconds,
                    verify_ssl=self.settings.recipe_catalog_verify_ssl,
                )
            )
            graph_context = environment.query_context(
                graph.graph_path,
                (
                    f"{goal.description} Java version source target release compiler build "
                    "dependencies imports deprecated APIs Guava OpenJPA Cobertura Scala Jakarta"
                ),
            )[: self.settings.max_output_characters]
            evidence_facts = _planning_evidence_facts(snapshot, goal, artifact_context)
            evidence_corpus = "\n".join(
                (
                    "\n".join(evidence_facts),
                    goal.model_dump_json(indent=2),
                    snapshot.model_dump_json(indent=2),
                    graph.summary(),
                    graph_context,
                    artifact_context,
                    migration_failures.context,
                )
            )
            planner = create_planning_agent(
                self.settings.model,
                ollama_base_url=self.settings.litellm_api_base,
                openai_api_key=(
                    self.settings.openai_api_key.get_secret_value()
                    if self.settings.openai_api_key
                    else None
                ),
            )
            result = planner.run_sync(
                (
                    "Recommend applicable Java migration recipes for this goal: "
                    f"{goal.model_dump_json()}. Use only the supplied project artifacts and "
                    "catalog. Explain evidence, benefit, dependencies/order, and confidence. "
                    "This is planning only: do not execute anything."
                ),
                deps=PlanningDependencies(
                    snapshot=snapshot,
                    graph_summary=graph.summary(),
                    graph_context=graph_context,
                    artifact_context=artifact_context,
                    planner_rules=planner_rules,
                    evidence_facts=evidence_facts,
                    migration_failure_context=migration_failures.context,
                    failed_recipes=failed_recipes,
                    completed_recipes=migration_failures.completed_recipes,
                    recipe_catalog=(
                        f"Catalog index: {catalog.source}\n"
                        "Declarative recipe source array:\n"
                        + json.dumps(
                            [source.model_dump(mode="json") for source in recipe_sources],
                            indent=2,
                        )
                    ),
                    allowed_recipes=frozenset(source.recipe for source in recipe_sources),
                    recipe_artifacts={
                        source.recipe: (
                            "org.openrewrite.recipe:"
                            f"{source.repository.rsplit('/', maxsplit=1)[-1]}:RELEASE"
                        )
                        for source in recipe_sources
                    },
                    evidence_corpus=evidence_corpus,
                ),
            )
            plan = _with_deterministic_prerequisites(result.output, snapshot)
        else:
            plan = rule_based_plan(snapshot, goal)
            validate_plan(plan)
        report = RunReport(
            run_id=uuid4().hex,
            status=RunStatus.PLANNED,
            snapshot=snapshot,
            context_graph=graph,
            plan=plan,
        )
        self._save(report)
        return report

    def run(self, repository: Path, goal: ModernizationGoal, *, apply: bool = False) -> RunReport:
        """Run approved non-source-changing steps and stop at explicit review gates."""

        environment = self._environment(repository)
        report = ModernizationFactory(settings=self.settings, environment=environment).prepare(
            repository, goal
        )
        if report.snapshot.dirty and not self.settings.allow_dirty_worktree:
            report.status = RunStatus.NEEDS_REVIEW
            report.steps.append(
                StepResult(
                    step_id="safety",
                    status=RunStatus.NEEDS_REVIEW,
                    message="Working tree is dirty; commit or stash changes before apply mode.",
                )
            )
            self._save(report)
            return report
        if not apply:
            return report

        report.status = RunStatus.RUNNING
        build_command = environment.verification_command(report.snapshot)
        if not isinstance(report.plan, ModernizationPlan):
            raise ValueError("Recipe recommendation plans cannot be executed")
        for step in report.plan.steps:
            if step.strategy is StepStrategy.COMMAND:
                if build_command is None:
                    self._fail(report, step.id, "Unknown build system")
                    break
                evidence = environment.verify(
                    report.snapshot.repository,
                    build_command,
                    timeout=self.settings.command_timeout_seconds,
                    output_limit=self.settings.max_output_characters,
                )
                if not evidence.green:
                    self._fail(report, step.id, "Baseline verification failed", evidence)
                    break
                report.steps.append(
                    StepResult(
                        step_id=step.id,
                        status=RunStatus.COMPLETED,
                        message="Baseline verification passed",
                        verification=evidence,
                    )
                )
            elif step.strategy is StepStrategy.OPENREWRITE:
                if not step.recipe:
                    self._review(report, step.id, "No approved recipe matches this target")
                    break
                evidence = environment.dry_run_rewrite(
                    report.snapshot,
                    step.recipe,
                    timeout=self.settings.command_timeout_seconds,
                )
                if not evidence.green:
                    self._fail(report, step.id, "OpenRewrite dry run failed", evidence)
                    break
                self._review(
                    report,
                    step.id,
                    "OpenRewrite dry run passed; review its patch before apply",
                )
                report.steps[-1].verification = evidence
                break
            else:
                self._review(
                    report,
                    step.id,
                    f"{step.strategy.value} source changes require explicit review in v0.1",
                )
                break
        else:
            report.status = RunStatus.COMPLETED
        self._save(report)
        return report

    @staticmethod
    def _review(report: RunReport, step_id: str, message: str) -> None:
        report.status = RunStatus.NEEDS_REVIEW
        report.steps.append(
            StepResult(step_id=step_id, status=RunStatus.NEEDS_REVIEW, message=message)
        )

    @staticmethod
    def _fail(
        report: RunReport,
        step_id: str,
        message: str,
        evidence: VerificationResult | None = None,
    ) -> None:
        report.status = RunStatus.FAILED
        report.steps.append(
            StepResult(
                step_id=step_id,
                status=RunStatus.FAILED,
                message=message,
                verification=evidence,
            )
        )

    def _save(self, report: RunReport) -> None:
        state_dir = report.snapshot.repository / self.settings.state_directory / "runs"
        state_dir.mkdir(parents=True, exist_ok=True)
        stem = state_dir / report.run_id
        stem.with_suffix(".json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2) + "\n"
        )
        stem.with_suffix(".md").write_text(to_markdown(report))
        MigrationPlanStore(
            report.snapshot.repository, self.settings.state_directory
        ).save(report)


def _with_utf8_prerequisite(
    plan: RecipePlan, snapshot: DiscoverySnapshot
) -> RecipePlan:
    """Prepend the built-in encoding repair when discovery proves it is needed."""

    if not snapshot.non_utf8_application_properties:
        return plan
    evidence = [
        f"discovery.non_utf8_application_properties={path}"
        for path in snapshot.non_utf8_application_properties
    ]
    conversion = RecipeRecommendation(
        execution_type=ExecutionType.UTF8,
        recipe=UTF8_APPLICATION_PROPERTIES_RECIPE,
        reason="Application properties contain legacy non-UTF-8 bytes.",
        evidence=evidence,
        expected_benefit=(
            "Prevents newer Maven resource plugins from rejecting application properties."
        ),
        priority=RecipePriority.HIGH,
        confidence=1,
    )
    ordered = []
    for recommendation in plan.recommendations[:9]:
        if "UpgradeSpringBoot" in recommendation.recipe:
            dependencies = list(
                dict.fromkeys(
                    [UTF8_APPLICATION_PROPERTIES_RECIPE, *recommendation.depends_on]
                )
            )
            recommendation = recommendation.model_copy(
                update={"depends_on": dependencies}
            )
        ordered.append(recommendation)
    return RecipePlan(recommendations=[conversion, *ordered])


def _with_datasource_initialization_prerequisite(
    plan: RecipePlan, snapshot: DiscoverySnapshot
) -> RecipePlan:
    if not snapshot.deferred_datasource_initialization_properties:
        return plan
    prerequisite = RecipeRecommendation(
        execution_type=ExecutionType.SPRING_PROPERTIES,
        recipe=DEFER_DATASOURCE_INITIALIZATION_RECIPE,
        reason="data.sql depends on Hibernate-created JPA tables.",
        evidence=[
            f"discovery.deferred_datasource_initialization_properties={path}"
            for path in snapshot.deferred_datasource_initialization_properties
        ],
        expected_benefit=(
            "Runs data.sql after Hibernate creates the schema on Spring Boot 2.5+."
        ),
        priority=RecipePriority.HIGH,
        confidence=1,
    )
    ordered = []
    for recommendation in plan.recommendations[:9]:
        if "UpgradeSpringBoot" in recommendation.recipe:
            recommendation = recommendation.model_copy(
                update={
                    "depends_on": list(
                        dict.fromkeys(
                            [
                                DEFER_DATASOURCE_INITIALIZATION_RECIPE,
                                *recommendation.depends_on,
                            ]
                        )
                    )
                }
            )
        ordered.append(recommendation)
    return RecipePlan(recommendations=[prerequisite, *ordered])


def _with_deterministic_prerequisites(
    plan: RecipePlan, snapshot: DiscoverySnapshot
) -> RecipePlan:
    plan = _with_datasource_initialization_prerequisite(plan, snapshot)
    return _with_utf8_prerequisite(plan, snapshot)
