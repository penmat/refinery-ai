"""Migration agents: deterministic plan dispatch and scoped AI patch proposals."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic_ai import Agent, RunContext

from refinery_ai.jdks import (
    JdkResolver,
    baseline_java_candidates,
    project_runtime_java,
    recipe_target_java,
)
from refinery_ai.migrations.ai_repair import (
    MigrationRepairer,
    springdoc_repair_applicable,
)
from refinery_ai.migrations.contracts import MigrationTypeRunner
from refinery_ai.migrations.openrewrite import OpenRewriteMigrationRunner
from refinery_ai.migrations.spring_properties import (
    DEFER_DATASOURCE_INITIALIZATION_RECIPE,
    SpringPropertiesMigrationRunner,
)
from refinery_ai.migrations.utf8 import (
    UTF8_APPLICATION_PROPERTIES_RECIPE,
    Utf8PropertiesMigrationRunner,
)
from refinery_ai.migrations.worktrees import GitWorktreeManager
from refinery_ai.models import (
    DiscoverySnapshot,
    ExecutionType,
    MigrationRun,
    PatchProposal,
    RunStatus,
    StepResult,
    VerificationResult,
)
from refinery_ai.planning_artifacts import MigrationPlanStore
from refinery_ai.telemetry import observe_llm
from refinery_ai.verification import verification_command, verify


class MigrationAgent:
    """Load a persisted plan and dispatch each step by its execution type."""

    def __init__(
        self,
        store: MigrationPlanStore,
        *,
        runners: dict[ExecutionType, MigrationTypeRunner] | None = None,
        timeout: int = 900,
        output_limit: int = 20_000,
        build_command: Callable[[DiscoverySnapshot], list[str] | None] = verification_command,
        build_verifier: Callable[..., VerificationResult] = verify,
        progress: Callable[[str], None] | None = None,
        worktree_factory: type[GitWorktreeManager] = GitWorktreeManager,
        repairer: MigrationRepairer | None = None,
        max_repair_attempts: int = 1,
        jdk_resolver: JdkResolver | None = None,
    ) -> None:
        self.store = store
        self.runners = runners or {
            ExecutionType.OPENREWRITE: OpenRewriteMigrationRunner(),
            ExecutionType.UTF8: Utf8PropertiesMigrationRunner(),
            ExecutionType.SPRING_PROPERTIES: SpringPropertiesMigrationRunner(),
        }
        self.timeout = timeout
        self.output_limit = output_limit
        self.build_command = build_command
        self.build_verifier = build_verifier
        self.progress = progress or (lambda message: None)
        self.worktree_factory = worktree_factory
        self.repairer = repairer
        self.max_repair_attempts = max_repair_attempts
        self.jdk_resolver = jdk_resolver
        self._jdk_environments: dict[int, dict[str, str]] = {}

    def run(
        self,
        plan_id: str | None = None,
        *,
        apply: bool = False,
        publish: bool = True,
        expected_profile: str | None = None,
    ) -> MigrationRun:
        plan = self.store.load(plan_id)
        if expected_profile is not None and plan.profile != expected_profile:
            actual = plan.profile or "no profile"
            raise ValueError(
                f"Migration plan uses {actual}; requested profile is {expected_profile}"
            )
        total = len(plan.steps)
        self.progress(
            f"Reading plan {plan.plan_id} ... about to run {total} "
            f"migration{'s' if total != 1 else ''}."
        )
        results: list[StepResult] = []
        status = RunStatus.COMPLETED
        run_id = uuid4().hex
        worktrees = None
        starting_revision = None
        current_revision = None
        baseline_verification = None
        baseline_artifacts = None
        current_java = project_runtime_java(plan.snapshot.java_version)
        baseline_candidates = baseline_java_candidates(
            plan.snapshot.java_version, [step.recipe for step in plan.steps]
        )
        required_jdks = {
            major
            for major in [
                current_java,
                *(recipe_target_java(step.recipe) for step in plan.steps),
            ]
            if major is not None
        }
        if self.jdk_resolver is not None:
            for major in sorted(required_jdks):
                self._jdk_environments[major] = self.jdk_resolver.environment(major) or {}
        if apply:
            worktrees = self.worktree_factory(
                self.store.repository,
                self.store.state_directory,
                run_id,
                plan.plan_id,
            )
            self.progress("Preparing isolated migration worktrees ...")
            starting_revision = worktrees.start()
            current_revision = starting_revision
            command = self.build_command(plan.snapshot)
            if command is None:
                self.progress("Baseline cannot be verified: unknown build system")
                status = RunStatus.FAILED
            else:
                attempts: list[tuple[int, VerificationResult]] = []
                if self.jdk_resolver is None or not baseline_candidates:
                    self.progress("Verifying baseline before Migration 1 ...")
                    baseline_verification = self.build_verifier(
                        Path(plan.snapshot.repository),
                        command,
                        timeout=self.timeout,
                        output_limit=self.output_limit,
                        environment=self._jdk_environment(current_java),
                    )
                else:
                    for major in baseline_candidates:
                        try:
                            environment = self._jdk_environment(major)
                        except RuntimeError as error:
                            self.progress(f"Cannot try baseline with JDK {major}: {error}")
                            continue
                        self.progress(
                            f"Verifying baseline before Migration 1 with installed JDK {major} ..."
                        )
                        candidate = self.build_verifier(
                            Path(plan.snapshot.repository),
                            command,
                            timeout=self.timeout,
                            output_limit=self.output_limit,
                            environment=environment,
                        )
                        attempts.append((major, candidate))
                        baseline_verification = candidate
                        if candidate.green:
                            current_java = major
                            self.progress(
                                f"Baseline verified with JDK {major}; using it for migrations ..."
                            )
                            break
                        self.progress(f"Baseline failed with JDK {major}.")
                if baseline_verification is None:
                    raise RuntimeError("No installed JDK was available for baseline verification")
                baseline_artifacts = worktrees.record_baseline(
                    baseline_verification, attempts or None
                )
                if not baseline_verification.green:
                    self.progress(
                        "Baseline build failed; no migrations were run. "
                        "This is not a migration regression."
                    )
                    status = RunStatus.FAILED
        runnable_steps = plan.steps if status is not RunStatus.FAILED else []
        for index, step in enumerate(runnable_steps, start=1):
            runner = self.runners.get(step.execution_type)
            step_id = f"{index}-{step.execution_type.value}"
            label = migration_display_name(step.recipe)
            self.progress(f"Running Migration {index}/{total}: {label}")
            if runner is None:
                self.progress(f"Migration {index}/{total} has an unsupported type: {label}")
                results.append(
                    StepResult(
                        step_id=step_id,
                        status=RunStatus.FAILED,
                        message=f"Unsupported migration type: {step.execution_type.value}",
                    )
                )
                status = RunStatus.FAILED
                break
            workspace = None
            step_snapshot = plan.snapshot
            if worktrees is not None:
                assert current_revision is not None
                workspace = worktrees.create_step(index, current_revision)
                step_snapshot = plan.snapshot.model_copy(
                    update={"repository": workspace.path}
                )
            execution = runner.run(
                step_snapshot,
                step,
                apply=apply,
                timeout=self.timeout,
                environment=self._jdk_environment(current_java),
            )
            if not execution.green:
                self.progress(f"Migration {index}/{total} failed: {label}")
                failure_artifacts = None
                if worktrees is not None and workspace is not None:
                    failure_artifacts = worktrees.record_failure(
                        workspace,
                        index,
                        step,
                        "Migration command failed",
                        execution,
                        None,
                    )
                results.append(
                    StepResult(
                        step_id=step_id,
                        status=RunStatus.FAILED,
                        message=f"Migration failed: {step.recipe}",
                        execution=execution,
                        attempts=1,
                        workspace=workspace.path if workspace else None,
                        failure_artifacts=failure_artifacts,
                    )
                )
                status = RunStatus.FAILED
                break

            build_result = None
            repairs: list[VerificationResult] = []
            if apply:
                target_java = recipe_target_java(step.recipe)
                if target_java is not None and target_java != current_java:
                    current_java = target_java
                    if self.jdk_resolver is not None:
                        self.progress(
                            f"Switching verification runtime to installed JDK {target_java} ..."
                        )
                self.progress(f"Verifying Migration {index}/{total}: {label}")
                command = self.build_command(step_snapshot)
                if command is None:
                    self.progress(
                        f"Migration {index}/{total} cannot be verified: unknown build system"
                    )
                    failure_artifacts = None
                    if worktrees is not None and workspace is not None:
                        failure_artifacts = worktrees.record_failure(
                            workspace,
                            index,
                            step,
                            "Unknown build system",
                            execution,
                            None,
                        )
                    results.append(
                        StepResult(
                            step_id=step_id,
                            status=RunStatus.FAILED,
                            message=(
                                "Cannot verify migration for unknown build system: "
                                f"{step.recipe}"
                            ),
                            execution=execution,
                            attempts=1,
                            workspace=workspace.path if workspace else None,
                            failure_artifacts=failure_artifacts,
                        )
                    )
                    status = RunStatus.FAILED
                    break
                build_result = self.build_verifier(
                    Path(step_snapshot.repository),
                    command,
                    timeout=self.timeout,
                    output_limit=self.output_limit,
                    environment=self._jdk_environment(current_java),
                )
                while (
                    not build_result.green
                    and self.repairer is not None
                    and workspace is not None
                    and len(repairs) < self.max_repair_attempts
                    and springdoc_repair_applicable(workspace.path, build_result)
                ):
                    attempt = len(repairs) + 1
                    self.progress(
                        f"Attempting local/scoped repair {attempt}/"
                        f"{self.max_repair_attempts}: {label}"
                    )
                    with observe_llm(
                        "repair",
                        run_id=run_id,
                        plan_id=plan.plan_id,
                        repair_attempt=attempt,
                    ):
                        repair = self.repairer.repair(workspace.path, step, build_result)
                    repairs.append(repair)
                    if not repair.green:
                        detail = repair.stderr.strip() or "no diagnostic was returned"
                        self.progress(f"Repair attempt {attempt} failed: {detail}")
                        break
                    self.progress(f"Verifying repair {attempt}: {label}")
                    build_result = self.build_verifier(
                        Path(step_snapshot.repository),
                        command,
                        timeout=self.timeout,
                        output_limit=self.output_limit,
                        environment=self._jdk_environment(current_java),
                    )
                if not build_result.green:
                    self.progress(f"Migration {index}/{total} failed verification: {label}")
                    failure_artifacts = None
                    if worktrees is not None and workspace is not None:
                        failure_artifacts = worktrees.record_failure(
                            workspace,
                            index,
                            step,
                            "Post-migration build failed",
                            execution,
                            build_result,
                        )
                        if repairs:
                            (failure_artifacts / "repairs.json").write_text(
                                json.dumps(
                                    [repair.model_dump(mode="json") for repair in repairs],
                                    indent=2,
                                )
                                + "\n"
                            )
                    results.append(
                        StepResult(
                            step_id=step_id,
                            status=RunStatus.FAILED,
                            message=f"Post-migration build failed: {step.recipe}",
                            execution=execution,
                            verification=build_result,
                            repairs=repairs,
                            attempts=1 + len(repairs),
                            workspace=workspace.path if workspace else None,
                            failure_artifacts=failure_artifacts,
                        )
                    )
                    status = RunStatus.FAILED
                    break

            checkpoint = None
            if worktrees is not None and workspace is not None:
                assert build_result is not None
                checkpoint = worktrees.checkpoint(
                    workspace, index, step, execution, build_result, label
                )
                current_revision = checkpoint

            results.append(
                StepResult(
                    step_id=step_id,
                    status=RunStatus.COMPLETED,
                    message=(
                        f"{step.execution_type.value} "
                        f"{'applied and build passed' if apply else 'dry run completed'}: "
                        f"{step.recipe}"
                    ),
                    execution=execution,
                    verification=build_result,
                    repairs=repairs,
                    attempts=1 + len(repairs),
                    workspace=workspace.path if workspace else None,
                    checkpoint=checkpoint,
                )
            )
            self.progress(f"Migration {index}/{total} completed: {label}")
        published_revision = None
        if worktrees is not None:
            assert current_revision is not None
            assert starting_revision is not None
            has_publishable_change = current_revision != starting_revision
            if (
                publish
                and baseline_verification is not None
                and baseline_verification.green
                and has_publishable_change
            ):
                self.progress("Applying the last verified checkpoint to the source checkout ...")
                published_revision = worktrees.publish(starting_revision, current_revision)
                self.progress(f"Applied verified result: {published_revision[:12]}")
            worktrees.finish(status.value, current_revision, published_revision)
        return MigrationRun(
            plan_id=plan.plan_id,
            status=status,
            run_id=run_id,
            starting_revision=starting_revision,
            final_revision=current_revision,
            published_revision=published_revision,
            baseline_verification=baseline_verification,
            baseline_artifacts=baseline_artifacts,
            steps=results,
        )

    def _jdk_environment(self, major: int | None) -> dict[str, str] | None:
        if self.jdk_resolver is None:
            return None
        if major is None:
            return None
        if major not in self._jdk_environments:
            self._jdk_environments[major] = self.jdk_resolver.environment(major) or {}
        return self._jdk_environments[major]


def migration_display_name(recipe: str) -> str:
    """Turn a fully qualified recipe class into a concise progress label."""

    if recipe == UTF8_APPLICATION_PROPERTIES_RECIPE:
        return "Convert application properties to UTF-8"
    if recipe == DEFER_DATASOURCE_INITIALIZATION_RECIPE:
        return "Defer datasource initialization"
    name = recipe.rsplit(".", maxsplit=1)[-1]
    java_upgrade = re.fullmatch(r"Java(\d+)(?:to|To)Java(\d+)", name)
    if java_upgrade:
        return f"Java {java_upgrade.group(1)} → Java {java_upgrade.group(2)}"
    upgrade = re.fullmatch(r"UpgradeToJava(\d+)", name)
    if upgrade:
        return f"Upgrade to Java {upgrade.group(1)}"
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return words.replace(" To ", " → ")


@dataclass(frozen=True)
class MigrationDependencies:
    snapshot: DiscoverySnapshot
    allowed_files: tuple[str, ...]


def create_migration_agent(model: str) -> Agent[MigrationDependencies, PatchProposal]:
    """Create a migration agent that proposes, but never applies, a unified diff."""

    agent: Agent[MigrationDependencies, PatchProposal] = Agent(
        model,
        deps_type=MigrationDependencies,
        output_type=PatchProposal,
        system_prompt=(
            "Propose the smallest correct unified diff for the assigned migration step. "
            "Only touch explicitly allowed files. Do not weaken tests or add unrelated changes."
        ),
    )

    @agent.instructions
    def migration_scope(ctx: RunContext[MigrationDependencies]) -> str:
        return f"Allowed files: {', '.join(ctx.deps.allowed_files)}"

    return agent
