"""Baseline preparation and JDK verification operations for the migration graph."""

import contextlib
import time
from pathlib import Path
from uuid import uuid4

from refinery_ai.jdks import baseline_java_candidates, project_runtime_java, recipe_target_java
from refinery_ai.migrations.execution_context import MigrationExecutionContext
from refinery_ai.migrations.execution_state import (
    BaselineReceipt,
    MigrationExecutionState,
    RunManifest,
)
from refinery_ai.migrations.worktrees import GitWorktreeManager
from refinery_ai.models import VerificationResult


def prepare_run(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    total = len(state.plan.steps)
    ctx.progress(
        f"Reading plan {state.plan.plan_id} ... about to run {total} migration"
        + ("s" if total != 1 else "")
        + "."
    )
    state.current_java = project_runtime_java(state.plan.snapshot.java_version)
    required = {
        value
        for value in [
            state.current_java,
            *(recipe_target_java(step.recipe) for step in state.plan.steps),
        ]
        if value is not None
    }
    if ctx.runner.jdk_resolver is not None:
        for major in sorted(required):
            ctx.runner._jdk_environments[major] = ctx.runner.jdk_resolver.environment(major) or {}
    if state.apply:
        ctx.progress("Preparing isolated migration worktrees ...")
        ctx.worktrees = (
            GitWorktreeManager(
                ctx.runner.store.repository,
                ctx.runner.store.state_directory,
                state.run_id,
                state.plan.plan_id,
            )
            if ctx.runner.worktree_factory is GitWorktreeManager
            else ctx.runner.worktree_factory(
                ctx.runner.store.repository,
                ctx.runner.store.state_directory,
                state.run_id,
                state.plan.plan_id,
            )
        )
        state.starting_revision = ctx.worktrees.start()
        state.current_revision = state.starting_revision
        assert ctx.store is not None
        ctx.store.create(
            RunManifest(
                run_id=state.run_id,
                repository=ctx.runner.store.repository,
                state_directory=ctx.runner.store.state_directory,
                plan=state.plan,
                starting_revision=state.starting_revision,
                policy=ctx.policy(),
                publish=state.publish,
            )
        )
        ctx.store.save_generation(0)


def _build(
    ctx: MigrationExecutionContext,
    state: MigrationExecutionState,
    repository: Path,
    command: list[str],
    java: int | None,
) -> VerificationResult:
    metadata = state.model_copy(update={"current_java": java})
    execution = uuid4().hex
    started = time.monotonic()
    if ctx.reporter is not None:
        ctx.reporter.emit("baseline_build", "started", metadata, execution)
    try:
        result = ctx.runner.build_verifier(
            repository,
            command,
            timeout=ctx.runner.timeout,
            output_limit=ctx.runner.output_limit,
            environment=ctx.environment(java),
        )
        if ctx.reporter is not None:
            ctx.reporter.emit(
                "baseline_build",
                "completed",
                metadata,
                execution,
                duration=time.monotonic() - started,
                outcome="verification_passed" if result.green else "verification_failed",
            )
        return result
    except BaseException as error:
        if ctx.reporter is not None:
            with contextlib.suppress(Exception):
                ctx.reporter.emit(
                    "baseline_build",
                    "cancelled" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed",
                    metadata,
                    execution,
                )
        raise


def verify_baseline(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    assert ctx.worktrees is not None and state.starting_revision is not None
    if (
        ctx.request.trusted_baseline_revision == state.starting_revision
        and ctx.request.trusted_baseline_java is not None
    ):
        state.current_java = ctx.request.trusted_baseline_java
        state.baseline_verification = VerificationResult(
            command=["refinery", "verified-checkpoint"],
            exit_code=0,
            stdout=f"Reused verified checkpoint {state.starting_revision} with JDK {state.current_java}",
            duration_seconds=0,
        )
        ctx.progress(
            "Reusing the previous cycle's verified checkpoint; skipping baseline build ..."
        )
    else:
        workspace = ctx.worktrees.create_baseline(state.starting_revision)
        snapshot = state.plan.snapshot.model_copy(update={"repository": workspace.path})
        command = ctx.runner.build_command(snapshot)
        if command is None:
            ctx.progress("Baseline cannot be verified: unknown build system")
            ctx.worktrees.rollback(workspace)
            state.status = "failed"
            return
        candidates = baseline_java_candidates(
            state.plan.snapshot.java_version, [step.recipe for step in state.plan.steps]
        )
        attempts = []
        if ctx.runner.jdk_resolver is None or not candidates:
            ctx.progress("Verifying baseline before Migration 1 ...")
            state.baseline_verification = _build(
                ctx, state, workspace.path, command, state.current_java
            )
        else:
            for major in candidates:
                try:
                    ctx.environment(major)
                except RuntimeError as error:
                    ctx.progress(f"Cannot try baseline with JDK {major}: {error}")
                    continue
                ctx.progress(
                    f"Verifying baseline before Migration 1 with installed JDK {major} ..."
                )
                candidate = _build(ctx, state, workspace.path, command, major)
                attempts.append((major, candidate))
                state.baseline_verification = candidate
                if candidate.green:
                    state.current_java = major
                    ctx.progress(f"Baseline verified with JDK {major}; using it for migrations ...")
                    break
                ctx.progress(f"Baseline failed with JDK {major}.")
        if state.baseline_verification is None:
            raise RuntimeError("No installed JDK was available for baseline verification")
        state.baseline_artifacts = ctx.worktrees.record_baseline(
            state.baseline_verification, attempts or None
        )
        ctx.worktrees.rollback(workspace)
        if not state.baseline_verification.green:
            ctx.progress(
                "Baseline build failed; no migrations were run. This is not a migration regression."
            )
            state.status = "failed"
    if state.baseline_verification is not None and state.baseline_verification.green:
        assert ctx.store is not None
        ctx.store.save_baseline(
            BaselineReceipt(
                revision=state.starting_revision,
                java=state.current_java,
                verification=state.baseline_verification,
                artifacts=state.baseline_artifacts,
            )
        )
