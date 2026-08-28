"""Modernization factory orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from refinery_ai.agents import PlanningDependencies, create_planning_agent
from refinery_ai.config import Settings
from refinery_ai.context_graph.contracts import ContextGraphBuildService
from refinery_ai.harness import AssessmentHarnessPolicy, HarnessRepositoryEnvironment
from refinery_ai.models import (
    DiscoverySnapshot,
    ModernizationGoal,
    ModernizationPlan,
    RecipePlan,
    RunReport,
    RunStatus,
    StepResult,
    StepStrategy,
    VerificationResult,
)
from refinery_ai.openrewrite import OpenRewriteAdapter
from refinery_ai.planning import rule_based_plan, validate_plan
from refinery_ai.planning_artifacts import load_planning_artifacts
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
                    evidence_facts=evidence_facts,
                    recipe_catalog=(
                        f"Catalog index: {catalog.source}\n"
                        "Declarative recipe source array:\n"
                        + json.dumps(
                            [source.model_dump(mode="json") for source in recipe_sources],
                            indent=2,
                        )
                    ),
                    allowed_recipes=frozenset(source.recipe for source in recipe_sources),
                    evidence_corpus=evidence_corpus,
                ),
            )
            plan = result.output
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
