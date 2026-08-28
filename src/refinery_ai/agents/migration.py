"""Migration agents: deterministic plan dispatch and scoped AI patch proposals."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic_ai import Agent, RunContext

from refinery_ai.migrations.contracts import MigrationTypeRunner
from refinery_ai.migrations.openrewrite import OpenRewriteMigrationRunner
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
    ) -> None:
        self.store = store
        self.runners = runners or {
            ExecutionType.OPENREWRITE: OpenRewriteMigrationRunner(),
        }
        self.timeout = timeout
        self.output_limit = output_limit
        self.build_command = build_command
        self.build_verifier = build_verifier
        self.progress = progress or (lambda message: None)
        self.worktree_factory = worktree_factory

    def run(
        self,
        plan_id: str | None = None,
        *,
        apply: bool = False,
        publish: bool = True,
    ) -> MigrationRun:
        plan = self.store.load(plan_id)
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
        for index, step in enumerate(plan.steps, start=1):
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
                step_snapshot, step, apply=apply, timeout=self.timeout
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
            if apply:
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
                    results.append(
                        StepResult(
                            step_id=step_id,
                            status=RunStatus.FAILED,
                            message=f"Post-migration build failed: {step.recipe}",
                            execution=execution,
                            verification=build_result,
                            attempts=1,
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
                    workspace, index, step, execution, build_result
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
                    attempts=1,
                    workspace=workspace.path if workspace else None,
                    checkpoint=checkpoint,
                )
            )
            self.progress(f"Migration {index}/{total} completed: {label}")
        published_revision = None
        if worktrees is not None:
            assert current_revision is not None
            assert starting_revision is not None
            if publish:
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
            steps=results,
        )


def migration_display_name(recipe: str) -> str:
    """Turn a fully qualified recipe class into a concise progress label."""

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
