"""Invocation-only services; never stored in a graph checkpoint."""
from __future__ import annotations
from typing import TYPE_CHECKING

from refinery_ai.migrations.execution_state import MigrationRequest, MigrationExecutionState, MigrationPolicy
from refinery_ai.migrations.repair import MigrationRepairStage, TransactionalMigrationRepairer
from refinery_ai.migrations.worktrees import GitWorktreeManager, StepWorkspace

if TYPE_CHECKING:
    from refinery_ai.migrations.runner import MigrationRunner
    from refinery_ai.migrations.run_store import MigrationRunStore


class MigrationExecutionContext:
    def __init__(self, runner: MigrationRunner, request: MigrationRequest, store: MigrationRunStore | None) -> None:
        self.runner = runner
        self.request = request
        self.store = store
        self.worktrees: GitWorktreeManager | None = None
        self.reporter = None
        self.pending: TransactionalMigrationRepairer | None = None
        self.stages = runner.repair_stages
        if self.stages is None:
            self.stages = ((MigrationRepairStage('repair', runner.repairer, runner.max_repair_attempts),)
                           if runner.repairer is not None else ())

    def policy(self) -> MigrationPolicy:
        return self.runner.policy or MigrationPolicy(
            stages=[(stage.name, stage.max_attempts, stage.uses_model) for stage in self.stages],
            timeout=self.runner.timeout, output_limit=self.runner.output_limit)

    def progress(self, message: str) -> None:
        self.runner.progress(message)

    def environment(self, java: int | None) -> dict[str, str] | None:
        return self.runner._jdk_environment(java)

    def workspace(self, state: MigrationExecutionState) -> StepWorkspace:
        assert state.workspace is not None
        return StepWorkspace(**state.workspace.model_dump())

    def rollback_pending(self) -> None:
        if self.pending is not None:
            self.pending.rollback_last()
            self.pending = None
