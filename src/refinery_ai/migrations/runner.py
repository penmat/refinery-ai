"""Deterministic migration plan orchestration and verified publication."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from refinery_ai.jdks import (
    JdkResolver,
    baseline_java_candidates,
    project_runtime_java,
    recipe_target_java,
)
from refinery_ai.migrations.contracts import MigrationTypeRunner
from refinery_ai.migrations.deterministic_repair import springdoc_repair_applicable
from refinery_ai.migrations.openrewrite import OpenRewriteMigrationRunner
from refinery_ai.migrations.repair import (
    MigrationRepairer,
    MigrationRepairStage,
    TransactionalMigrationRepairer,
)
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
    RunStatus,
    StepResult,
    VerificationResult,
)
from refinery_ai.planning_artifacts import MigrationPlanStore
from refinery_ai.telemetry import observe_llm
from refinery_ai.verification import verification_command, verify


def _failure_score(result: VerificationResult) -> tuple[int, int]:
    """Rank red verification results so model repairs can be kept only when they improve."""

    if result.green:
        return (0, 0)
    diagnostic = f"{result.stdout}\n{result.stderr}"
    compilation_markers = (
        "COMPILATION ERROR",
        "Compilation failure",
        "cannot find symbol",
        "package does not exist",
        "incompatible types:",
    )
    compilation_errors = sum(diagnostic.count(marker) for marker in compilation_markers)
    if compilation_errors:
        return (3, compilation_errors)
    test_summaries = re.findall(
        r"Tests run:\s*\d+,\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)", diagnostic
    )
    if test_summaries:
        failures, errors = test_summaries[-1]
        return (2, int(failures) + int(errors))
    return (4, 1)


class MigrationRunner:
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
        repair_stages: tuple[MigrationRepairStage, ...] | None = None,
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
        self.repair_stages = repair_stages
        self.jdk_resolver = jdk_resolver
        self._jdk_environments: dict[int, dict[str, str]] = {}

    def run(
        self,
        plan_id: str | None = None,
        *,
        apply: bool = False,
        publish: bool = True,
        expected_profile: str | None = None,
        trusted_baseline_revision: str | None = None,
        trusted_baseline_java: int | None = None,
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
            reuse_verified_baseline = (
                trusted_baseline_revision == starting_revision and trusted_baseline_java is not None
            )
            if reuse_verified_baseline:
                current_java = trusted_baseline_java
                baseline_verification = VerificationResult(
                    command=["refinery", "verified-checkpoint"],
                    exit_code=0,
                    stdout=(
                        f"Reused verified checkpoint {starting_revision} with JDK {current_java}"
                    ),
                    duration_seconds=0,
                )
                self.progress(
                    "Reusing the previous cycle's verified checkpoint; skipping baseline build ..."
                )
            else:
                baseline_workspace = worktrees.create_baseline(starting_revision)
                baseline_snapshot = plan.snapshot.model_copy(
                    update={"repository": baseline_workspace.path}
                )
                command = self.build_command(baseline_snapshot)
                if command is None:
                    self.progress("Baseline cannot be verified: unknown build system")
                    worktrees.rollback(baseline_workspace)
                    status = RunStatus.FAILED
                else:
                    attempts: list[tuple[int, VerificationResult]] = []
                    if self.jdk_resolver is None or not baseline_candidates:
                        self.progress("Verifying baseline before Migration 1 ...")
                        baseline_verification = self.build_verifier(
                            baseline_workspace.path,
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
                                "Verifying baseline before Migration 1 with installed "
                                f"JDK {major} ..."
                            )
                            candidate = self.build_verifier(
                                baseline_workspace.path,
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
                                    f"Baseline verified with JDK {major}; "
                                    "using it for migrations ..."
                                )
                                break
                            self.progress(f"Baseline failed with JDK {major}.")
                    if baseline_verification is None:
                        raise RuntimeError(
                            "No installed JDK was available for baseline verification"
                        )
                    baseline_artifacts = worktrees.record_baseline(
                        baseline_verification, attempts or None
                    )
                    worktrees.rollback(baseline_workspace)
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
                step_snapshot = plan.snapshot.model_copy(update={"repository": workspace.path})
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
                                f"Cannot verify migration for unknown build system: {step.recipe}"
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
                stages = self.repair_stages
                if stages is None and self.repairer is not None:
                    stages = (
                        MigrationRepairStage(
                            name="repair",
                            repairer=self.repairer,
                            max_attempts=self.max_repair_attempts,
                        ),
                    )
                for stage in stages or ():
                    for attempt in range(1, stage.max_attempts + 1):
                        if (
                            build_result.green
                            or workspace is None
                            or (
                                not stage.uses_model
                                and not springdoc_repair_applicable(workspace.path, build_result)
                            )
                        ):
                            break
                        self.progress(
                            f"Attempting {stage.name} repair {attempt}/"
                            f"{stage.max_attempts}: {label}"
                        )
                        if stage.uses_model:
                            with observe_llm(
                                "repair",
                                run_id=run_id,
                                plan_id=plan.plan_id,
                                repair_attempt=attempt,
                            ):
                                repair = stage.repairer.repair(workspace.path, step, build_result)
                        else:
                            repair = stage.repairer.repair(workspace.path, step, build_result)
                        repair = repair.model_copy(
                            update={"command": ["refinery", f"{stage.name.lower()}-repair"]}
                        )
                        repairs.append(repair)
                        if not repair.green:
                            detail = repair.stderr.strip() or "no diagnostic was returned"
                            self.progress(
                                f"{stage.name.capitalize()} repair attempt {attempt} "
                                f"did not apply: {detail}"
                            )
                            break
                        self.progress(f"Verifying {stage.name} repair {attempt}: {label}")
                        candidate_result = self.build_verifier(
                            Path(step_snapshot.repository),
                            command,
                            timeout=self.timeout,
                            output_limit=self.output_limit,
                            environment=self._jdk_environment(current_java),
                        )
                        if stage.uses_model and isinstance(
                            stage.repairer, TransactionalMigrationRepairer
                        ):
                            if candidate_result.green or _failure_score(
                                candidate_result
                            ) < _failure_score(build_result):
                                stage.repairer.accept_last()
                                build_result = candidate_result
                            else:
                                stage.repairer.rollback_last()
                                self.progress(
                                    f"Rolled back {stage.name} repair {attempt}: "
                                    "verification did not improve"
                                )
                        else:
                            build_result = candidate_result
                    if build_result.green:
                        break
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
            verified_java=current_java if status is RunStatus.COMPLETED else None,
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
