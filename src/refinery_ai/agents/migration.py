"""Migration agents: deterministic plan dispatch and scoped AI patch proposals."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent, RunContext

from refinery_ai.migrations.contracts import MigrationTypeRunner
from refinery_ai.migrations.openrewrite import OpenRewriteMigrationRunner
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

    def run(self, plan_id: str | None = None, *, apply: bool = False) -> MigrationRun:
        plan = self.store.load(plan_id)
        total = len(plan.steps)
        self.progress(
            f"Reading plan {plan.plan_id} ... about to run {total} "
            f"migration{'s' if total != 1 else ''}."
        )
        results: list[StepResult] = []
        status = RunStatus.COMPLETED
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
            execution = runner.run(
                plan.snapshot, step, apply=apply, timeout=self.timeout
            )
            if not execution.green:
                self.progress(f"Migration {index}/{total} failed: {label}")
                results.append(
                    StepResult(
                        step_id=step_id,
                        status=RunStatus.FAILED,
                        message=f"Migration failed: {step.recipe}",
                        execution=execution,
                        attempts=1,
                    )
                )
                status = RunStatus.FAILED
                break

            build_result = None
            if apply:
                self.progress(f"Verifying Migration {index}/{total}: {label}")
                command = self.build_command(plan.snapshot)
                if command is None:
                    self.progress(
                        f"Migration {index}/{total} cannot be verified: unknown build system"
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
                        )
                    )
                    status = RunStatus.FAILED
                    break
                build_result = self.build_verifier(
                    Path(plan.snapshot.repository),
                    command,
                    timeout=self.timeout,
                    output_limit=self.output_limit,
                )
                if not build_result.green:
                    self.progress(f"Migration {index}/{total} failed verification: {label}")
                    results.append(
                        StepResult(
                            step_id=step_id,
                            status=RunStatus.FAILED,
                            message=f"Post-migration build failed: {step.recipe}",
                            execution=execution,
                            verification=build_result,
                            attempts=1,
                        )
                    )
                    status = RunStatus.FAILED
                    break

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
                )
            )
            self.progress(f"Migration {index}/{total} completed: {label}")
        return MigrationRun(plan_id=plan.plan_id, status=status, steps=results)


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
