"""Migration agents: deterministic plan dispatch and scoped AI patch proposals."""

from __future__ import annotations

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
    ) -> None:
        self.store = store
        self.runners = runners or {
            ExecutionType.OPENREWRITE: OpenRewriteMigrationRunner(),
        }
        self.timeout = timeout
        self.output_limit = output_limit
        self.build_command = build_command
        self.build_verifier = build_verifier

    def run(self, plan_id: str | None = None, *, apply: bool = False) -> MigrationRun:
        plan = self.store.load(plan_id)
        results: list[StepResult] = []
        status = RunStatus.COMPLETED
        for index, step in enumerate(plan.steps, start=1):
            runner = self.runners.get(step.execution_type)
            step_id = f"{index}-{step.execution_type.value}"
            if runner is None:
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
                command = self.build_command(plan.snapshot)
                if command is None:
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
        return MigrationRun(plan_id=plan.plan_id, status=status, steps=results)


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
