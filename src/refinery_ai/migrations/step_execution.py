"""Recipe effects, verified checkpoints and publication for migration graph nodes."""

import json

from refinery_ai.jdks import recipe_target_java
from refinery_ai.migrations.execution_context import MigrationExecutionContext
from refinery_ai.migrations.execution_state import (
    MigrationExecutionState,
    PublicationIntent,
    StepReceipt,
    WorkspaceState,
)
from refinery_ai.models import MigrationRun, RunStatus, StepResult


def label(state: MigrationExecutionState) -> str:
    from refinery_ai.migrations.runner import migration_display_name

    return migration_display_name(state.plan.steps[state.index - 1].recipe)


def prepare_step(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    step = state.plan.steps[state.index - 1]
    ctx.progress(f"Running Migration {state.index}/{len(state.plan.steps)}: {label(state)}")
    state.workspace = None
    state.execution = state.build = state.candidate = None
    state.repairs = []
    state.stage_index = state.attempt = 0
    state.pending = False
    state.failure_reason = state.failure_message = ""
    if step.execution_type not in ctx.runner.runners:
        ctx.progress(
            f"Migration {state.index}/{len(state.plan.steps)} "
            f"has an unsupported type: {label(state)}"
        )
        state.failure_message = f"Unsupported migration type: {label(state)}"
        return
    if ctx.worktrees is not None:
        assert state.current_revision is not None
        workspace = ctx.worktrees.create_step(state.index, state.current_revision)
        state.workspace = WorkspaceState(
            path=workspace.path,
            artifact_directory=workspace.artifact_directory,
            base_revision=workspace.base_revision,
        )


def execute_recipe(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    step = state.plan.steps[state.index - 1]
    snapshot = state.plan.snapshot
    if state.workspace is not None:
        snapshot = snapshot.model_copy(update={"repository": state.workspace.path})
    state.execution = ctx.runner.runners[step.execution_type].run(
        snapshot,
        step,
        apply=state.apply,
        timeout=ctx.runner.timeout,
        environment=ctx.environment(state.current_java),
    )
    if not state.execution.green:
        ctx.progress(f"Migration {state.index}/{len(state.plan.steps)} failed: {label(state)}")
        state.failure_reason = "Migration command failed"
        state.failure_message = f"Migration failed: {step.recipe}"


def verify_candidate(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    assert state.workspace is not None
    step = state.plan.steps[state.index - 1]
    snapshot = state.plan.snapshot.model_copy(update={"repository": state.workspace.path})
    if state.pending:
        stage = ctx.stages[state.stage_index]
        ctx.progress(f"Verifying {stage.name} repair {state.attempt}: {label(state)}")
    else:
        target = recipe_target_java(step.recipe)
        if target is not None and target != state.current_java:
            state.current_java = target
            if ctx.runner.jdk_resolver is not None:
                ctx.progress(f"Switching verification runtime to installed JDK {target} ...")
        ctx.progress(f"Verifying Migration {state.index}/{len(state.plan.steps)}: {label(state)}")
    command = ctx.runner.build_command(snapshot)
    if command is None:
        ctx.progress(
            f"Migration {state.index}/{len(state.plan.steps)} "
            "cannot be verified: unknown build system"
        )
        state.failure_reason = "Unknown build system"
        state.failure_message = f"Cannot verify migration for unknown build system: {step.recipe}"
        return
    result = ctx.runner.build_verifier(
        state.workspace.path,
        command,
        timeout=ctx.runner.timeout,
        output_limit=ctx.runner.output_limit,
        environment=ctx.environment(state.current_java),
    )
    state.outcome = "verification_passed" if result.green else "verification_failed"
    if state.pending:
        state.candidate = result
    else:
        state.build = result


def checkpoint_step(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    step = state.plan.steps[state.index - 1]
    checkpoint = None
    if ctx.worktrees is not None:
        assert state.execution is not None and state.build is not None and state.build.green
        workspace = ctx.workspace(state)
        checkpoint = ctx.worktrees.checkpoint(
            workspace, state.index, step, state.execution, state.build, label(state)
        )
        state.current_revision = checkpoint
    result = StepResult(
        step_id=f"{state.index}-{step.execution_type.value}",
        status=RunStatus.COMPLETED,
        message=f"{step.execution_type.value} "
        + ("applied and build passed" if state.apply else "dry run completed")
        + f": {step.recipe}",
        execution=state.execution,
        verification=state.build,
        repairs=state.repairs,
        attempts=1 + len(state.repairs),
        workspace=state.workspace.path if state.workspace else None,
        checkpoint=checkpoint,
    )
    if ctx.store is not None:
        assert (
            state.workspace is not None
            and state.execution is not None
            and state.build is not None
            and checkpoint is not None
        )
        manifest = ctx.store.load_manifest()
        ctx.store.save_step(
            StepReceipt(
                run_id=state.run_id,
                plan_digest=manifest.plan_digest,
                step=state.index,
                recipe=step,
                base_revision=state.workspace.base_revision,
                checkpoint=checkpoint,
                generation=state.generation,
                java=state.current_java,
                execution=state.execution,
                verification=state.build,
                workspace=state.workspace.path,
                result=result,
            )
        )
    state.results.append(result)
    ctx.progress(f"Migration {state.index}/{len(state.plan.steps)} completed: {label(state)}")
    state.index += 1


def record_step_failure(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    step = state.plan.steps[state.index - 1]
    artifacts = None
    if state.failure_reason == "Post-migration build failed":
        ctx.progress(
            f"Migration {state.index}/{len(state.plan.steps)} failed verification: {label(state)}"
        )
    if ctx.worktrees is not None and state.workspace is not None:
        artifacts = ctx.worktrees.record_failure(
            ctx.workspace(state),
            state.index,
            step,
            state.failure_reason,
            state.execution,
            state.build,
        )
        if state.repairs:
            (artifacts / "repairs.json").write_text(
                json.dumps([item.model_dump(mode="json") for item in state.repairs], indent=2)
                + "\n"
            )
    state.results.append(
        StepResult(
            step_id=f"{state.index}-{step.execution_type.value}",
            status=RunStatus.FAILED,
            message=state.failure_message,
            execution=state.execution,
            verification=state.build,
            repairs=state.repairs,
            attempts=1 + len(state.repairs) if state.execution is not None else 0,
            workspace=state.workspace.path if state.workspace else None,
            failure_artifacts=artifacts,
        )
    )
    state.status = "failed"


def publish_result(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    if ctx.worktrees is None:
        return
    assert state.starting_revision is not None and state.current_revision is not None
    if (
        state.publish
        and state.baseline_verification is not None
        and state.baseline_verification.green
        and state.current_revision != state.starting_revision
    ):
        intent = PublicationIntent(
            starting_revision=state.starting_revision, target_revision=state.current_revision
        )
        assert ctx.store is not None
        ctx.store.save_publication(intent)
        ctx.progress("Applying the last verified checkpoint to the source checkout ...")
        current = ctx.worktrees._git(ctx.runner.store.repository, "rev-parse", "HEAD").strip()
        state.published_revision = (
            current
            if current == intent.target_revision
            else ctx.worktrees.publish(state.starting_revision, state.current_revision)
        )
        ctx.store.save_publication(intent.model_copy(update={"completed": True}))
        ctx.progress(f"Applied verified result: {state.published_revision[:12]}")
    ctx.worktrees.finish(state.status, state.current_revision, state.published_revision)


def finish_run(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> MigrationRun:
    result = MigrationRun(
        plan_id=state.plan.plan_id,
        status=RunStatus(state.status),
        run_id=state.run_id,
        starting_revision=state.starting_revision,
        final_revision=state.current_revision,
        published_revision=state.published_revision,
        verified_java=state.current_java if state.status == "completed" else None,
        baseline_verification=state.baseline_verification,
        baseline_artifacts=state.baseline_artifacts,
        steps=state.results,
    )
    if ctx.store is not None:
        ctx.store.save_result(result)
    return result
