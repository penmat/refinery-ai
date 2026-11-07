"""Explicit migration workflow, shared by standalone and nested profile execution."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable, Iterator
from contextvars import Context, copy_context
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from refinery_ai.migrations import baseline, repair_execution, step_execution
from refinery_ai.migrations.events import MigrationReporter
from refinery_ai.migrations.execution_context import MigrationExecutionContext
from refinery_ai.migrations.execution_state import (
    MigrationExecutionState,
    MigrationRequest,
    RecoveryPoint,
    RunManifest,
)
from refinery_ai.migrations.process_safety import MigrationLease
from refinery_ai.migrations.run_store import MigrationRunStore
from refinery_ai.models import MigrationRun, PersistedMigrationPlan
from refinery_ai.monitor.recording import (
    InvocationContext,
    ProfileReporter,
    capture_usage,
)


class MigrationGraphState(TypedDict, total=False):
    data: MigrationExecutionState
    result: MigrationRun
    migration_request: MigrationRequest
    migration_result: MigrationRun


def initial_migration_state(
    plan: PersistedMigrationPlan,
    request: MigrationRequest,
    manifest: RunManifest | None,
    recovery: RecoveryPoint | None = None,
) -> MigrationGraphState:
    state = MigrationExecutionState(
        plan=plan,
        run_id=manifest.run_id if manifest else uuid4().hex,
        apply=request.apply,
        publish=manifest.publish if manifest else request.publish,
    )
    if recovery is not None and manifest is not None:
        state.generation = recovery.generation
        state.index = recovery.next_step
        state.results = recovery.results
        state.starting_revision = manifest.starting_revision
        state.current_revision = recovery.revision
        state.current_java = recovery.java
        if recovery.baseline is not None:
            state.baseline_verification = recovery.baseline.verification
            state.baseline_artifacts = recovery.baseline.artifacts
    return {"data": state}


def build_migration_graph(
    context: MigrationExecutionContext | None,
    *,
    checkpointer: Any = None,
    nested: NestedMigration | None = None,
) -> Any:
    graph = StateGraph(MigrationGraphState)
    operations: dict[str, Callable[[MigrationExecutionContext, MigrationExecutionState], None]] = {
        "prepare_run": baseline.prepare_run,
        "verify_baseline": baseline.verify_baseline,
        "prepare_step": step_execution.prepare_step,
        "execute_recipe": step_execution.execute_recipe,
        "verify_candidate": step_execution.verify_candidate,
        "select_repair": repair_execution.select_repair,
        "repair": repair_execution.repair,
        "evaluate_repair": repair_execution.evaluate_repair,
        "checkpoint_step": step_execution.checkpoint_step,
        "record_step_failure": step_execution.record_step_failure,
        "publish_result": step_execution.publish_result,
    }

    def node(
        name: str,
        operation: Callable[
            [MigrationExecutionContext, MigrationExecutionState], MigrationRun | None
        ],
    ) -> Any:
        def invoke(state: MigrationGraphState) -> MigrationGraphState:
            ctx = nested.context if nested is not None else context
            assert ctx is not None
            value = state["data"].model_copy(deep=True)
            before = value.model_copy(deep=True)
            execution_id = uuid4().hex
            started = time.monotonic()
            stage = (
                ctx.stages[value.stage_index].name
                if value.stage_index < len(ctx.stages) and value.attempt
                else None
            )
            reporter = ctx.reporter
            if reporter is not None:
                reporter.emit(name, "started", before, execution_id, stage=stage)

            def execute() -> MigrationRun | None:
                value.outcome = None
                if reporter is None:
                    return operation(ctx, value)
                else:
                    with capture_usage(
                        lambda input_tokens, output_tokens: reporter.emit(
                            name,
                            "usage",
                            before,
                            execution_id,
                            stage=stage,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )
                    ):
                        return operation(ctx, value)

            try:
                result = nested.scope.run(execute) if nested else execute()
                if reporter is not None:
                    reporter.emit(
                        name,
                        "completed",
                        before,
                        execution_id,
                        duration=time.monotonic() - started,
                        outcome=value.outcome
                        or (result.status.value if isinstance(result, MigrationRun) else None),
                        failure_reason=value.failure_reason or value.failure_message or None,
                        checkpoint=value.current_revision,
                        stage=stage,
                    )
                update: MigrationGraphState = {"data": value}
                if isinstance(result, MigrationRun):
                    if nested is None:
                        update["result"] = result
                    else:
                        update["migration_result"] = result
                return update
            except BaseException as error:
                if reporter is not None:
                    with contextlib.suppress(Exception):
                        reporter.emit(
                            name,
                            "cancelled"
                            if isinstance(error, (KeyboardInterrupt, SystemExit))
                            else "failed",
                            before,
                            execution_id,
                            duration=time.monotonic() - started,
                            stage=stage,
                        )
                raise

        return invoke

    for name, operation in operations.items():
        graph.add_node(name, node(name, operation))

    def recover(ctx: MigrationExecutionContext, value: MigrationExecutionState) -> None:
        ctx.worktrees = ctx.runner.worktree_factory(
            ctx.runner.store.repository,
            ctx.runner.store.state_directory,
            value.run_id,
            value.plan.plan_id,
        )
        ctx.worktrees.generation = value.generation

    graph.add_node("recover_run", node("recover_run", recover))
    graph.add_node("finish_run", node("finish_run", step_execution.finish_run))
    entry = START
    if nested is not None:
        graph.add_node("initialize_migration", nested.initialize)
        graph.add_edge(START, "initialize_migration")
        entry = "initialize_migration"
    graph.add_conditional_edges(
        entry,
        lambda s: (
            END if "data" not in s else ("recover_run" if s["data"].generation else "prepare_run")
        ),
        ["prepare_run", "recover_run", END]
        if nested is not None
        else ["prepare_run", "recover_run"],
    )
    graph.add_conditional_edges(
        "prepare_run",
        lambda s: (
            "verify_baseline"
            if s["data"].apply
            else "prepare_step"
            if s["data"].plan.steps
            else "publish_result"
        ),
        ["verify_baseline", "prepare_step", "publish_result"],
    )
    graph.add_conditional_edges(
        "recover_run",
        lambda s: (
            "verify_baseline"
            if s["data"].baseline_verification is None
            else "prepare_step"
            if s["data"].index <= len(s["data"].plan.steps)
            else "publish_result"
        ),
        ["verify_baseline", "prepare_step", "publish_result"],
    )

    def after_baseline(s: MigrationGraphState) -> str:
        return (
            "prepare_step"
            if s["data"].status == "completed" and s["data"].index <= len(s["data"].plan.steps)
            else "publish_result"
        )

    graph.add_conditional_edges(
        "verify_baseline", after_baseline, ["prepare_step", "publish_result"]
    )
    graph.add_conditional_edges(
        "prepare_step",
        lambda s: "record_step_failure" if s["data"].failure_message else "execute_recipe",
        ["record_step_failure", "execute_recipe"],
    )
    graph.add_conditional_edges(
        "execute_recipe",
        lambda s: (
            "record_step_failure"
            if s["data"].failure_reason
            else "verify_candidate"
            if s["data"].apply
            else "checkpoint_step"
        ),
        ["record_step_failure", "verify_candidate", "checkpoint_step"],
    )

    def after_verify(s: MigrationGraphState) -> str:
        value = s["data"]
        if value.failure_reason:
            return "record_step_failure"
        if value.pending:
            return "evaluate_repair"
        return (
            "checkpoint_step" if value.build is not None and value.build.green else "select_repair"
        )

    graph.add_conditional_edges(
        "verify_candidate",
        after_verify,
        ["record_step_failure", "evaluate_repair", "checkpoint_step", "select_repair"],
    )
    graph.add_conditional_edges(
        "select_repair",
        lambda s: "record_step_failure" if s["data"].failure_reason else "repair",
        ["record_step_failure", "repair"],
    )
    graph.add_conditional_edges(
        "repair",
        lambda s: "verify_candidate" if s["data"].pending else "select_repair",
        ["verify_candidate", "select_repair"],
    )
    graph.add_conditional_edges(
        "evaluate_repair",
        lambda s: (
            "checkpoint_step"
            if s["data"].build is not None and s["data"].build.green
            else "select_repair"
        ),
        ["checkpoint_step", "select_repair"],
    )
    graph.add_conditional_edges(
        "checkpoint_step",
        lambda s: (
            "prepare_step" if s["data"].index <= len(s["data"].plan.steps) else "publish_result"
        ),
        ["prepare_step", "publish_result"],
    )
    graph.add_edge("record_step_failure", "publish_result")
    graph.add_edge("publish_result", "finish_run")
    if nested is None:
        graph.add_edge("finish_run", END)
    else:
        graph.add_node("close_migration", nested.close_node)
        graph.add_edge("finish_run", "close_migration")
        graph.add_edge("close_migration", END)
    return graph.compile(
        checkpointer=checkpointer if checkpointer is not None or nested is not None else False
    )


@contextlib.contextmanager
def migration_invocation(
    runner: Any,
    request: MigrationRequest,
    *,
    standalone: bool,
    writer: Any = None,
    saved: MigrationExecutionState | None = None,
    parent: InvocationContext | None = None,
) -> Iterator[tuple[MigrationExecutionContext, MigrationGraphState, Any] | MigrationRun]:
    resume_id = request.resume_run_id
    if resume_id is None and request.resume_bound_run and request.plan_id is not None:
        resume_id = MigrationRunStore.find_bound_run(
            runner.store.repository, runner.store.state_directory, request.plan_id
        )
    manifest = None
    recovery = None
    if resume_id is not None:
        store = MigrationRunStore(runner.store.repository, runner.store.state_directory, resume_id)
        manifest = store.load_manifest()
        plan = manifest.plan
        if request.plan_id is not None and request.plan_id != plan.plan_id:
            raise ValueError("Resume plan does not match pinned plan")
        request = request.model_copy(update={"apply": True, "publish": manifest.publish})
    else:
        plan = runner.store.load(request.plan_id)
        store = None
    if request.expected_profile is not None and plan.profile != request.expected_profile:
        raise ValueError(
            f"Migration plan uses {plan.profile or 'no profile'}; "
            f"requested profile is {request.expected_profile}"
        )
    initial = initial_migration_state(plan, request, manifest)
    run_id = initial["data"].run_id
    if request.apply and store is None:
        store = MigrationRunStore(runner.store.repository, runner.store.state_directory, run_id)
    ctx = MigrationExecutionContext(runner, request, store)
    ctx.profile_checkpoint = not standalone
    with contextlib.ExitStack() as resources:
        lease = None
        if request.apply and os.name == "posix":
            lease = resources.enter_context(
                MigrationLease(runner.store.repository, runner.store.state_directory, run_id)
            )
        if resume_id is not None:
            if lease is None:
                raise ValueError("Safe resume requires POSIX support")
            from refinery_ai.migrations.recovery import recover_migration

            assert store is not None
            recovery = recover_migration(
                store, ctx.policy(), lease=lease, profile_checkpoint=not standalone, saved=saved
            )
            if recovery.result is not None:
                yield recovery.result
                return
            initial = initial_migration_state(plan, request, manifest, recovery)
        checkpointer = None
        if store is not None and standalone:
            checkpointer = resources.enter_context(
                SqliteSaver.from_conn_string(str(store.checkpoints))
            )
        parent_writer = writer
        if parent_writer is None:
            with contextlib.suppress(RuntimeError):
                parent_writer = get_stream_writer()
        ctx.reporter = MigrationReporter(
            store.journal if store is not None else None,
            runner.event_observer,
            stream_writer=parent_writer,
            parent=parent,
        )
        if recovery is not None:
            pending = {
                event.execution_id: event
                for event in ctx.reporter.history
                if event.lifecycle == "started"
            }
            for event in ctx.reporter.history:
                if event.lifecycle not in ("started", "usage"):
                    pending.pop(event.execution_id, None)
            for event in pending.values():
                old = initial["data"].model_copy(
                    update={
                        "index": event.step or initial["data"].index,
                        "generation": event.generation,
                        "attempt": event.repair_attempt or 0,
                    }
                )
                ctx.reporter.emit(
                    event.phase,
                    "interrupted",
                    old,
                    event.execution_id,
                    outcome="recovery_restarted",
                )
        error = None
        try:
            yield ctx, initial, checkpointer
        except BaseException as failure:
            error = failure
            raise
        finally:
            for cleanup in (ctx.rollback_pending, ctx.reporter.close):
                try:
                    cleanup()
                except BaseException as failure:
                    if error is None:
                        raise
                    error.add_note(f"Migration cleanup also failed: {type(failure).__name__}")


class NestedMigration:
    """Own process resources across child nodes, outside serialized graph state."""

    def __init__(
        self,
        factory: Callable[[MigrationRequest], Any] | None,
        reporter: ProfileReporter | None = None,
    ) -> None:
        self.saved: MigrationExecutionState | None = None
        self.factory = factory
        self.reporter = reporter
        self.parent: InvocationContext | None = None
        self.scope = Context()
        self.resources = contextlib.ExitStack()
        self.context: MigrationExecutionContext | None = None

    def initialize(self, state: MigrationGraphState) -> MigrationGraphState:
        assert self.factory is not None
        request = state["migration_request"]
        runner = self.factory(request)

        parent = InvocationContext(self.reporter.run_id, uuid4().hex) if self.reporter else None
        self.parent = parent
        self.started = time.monotonic()
        if parent and self.reporter:
            self.reporter.emit("migrate", "started", parent.execution_id)
        writer = get_stream_writer()
        self.scope = copy_context()

        def open_invocation() -> (
            tuple[MigrationExecutionContext, MigrationGraphState, Any] | MigrationRun
        ):
            return self.resources.enter_context(
                migration_invocation(
                    runner,
                    request,
                    standalone=False,
                    writer=writer,
                    saved=self.saved,
                    parent=parent,
                )
            )

        opened = self.scope.run(open_invocation)
        self.saved = None
        if isinstance(opened, MigrationRun):
            self.close_node({"migration_result": opened})
            return {"migration_result": opened}
        self.context, initial, _ = opened
        record_migration_topology(self.context, initial)
        return initial

    def close_node(self, state: MigrationGraphState) -> MigrationGraphState:
        self.scope.run(self.resources.close)
        self.context = None
        result = state.get("migration_result")
        self.report_completion("completed", child_run_id=result.run_id if result else None)
        return {}

    def report_completion(
        self,
        lifecycle: Literal["completed", "failed", "cancelled"],
        *,
        child_run_id: str | None = None,
    ) -> None:
        if self.parent and self.reporter:
            parent, self.parent = self.parent, None
            self.reporter.emit(
                "migrate",
                lifecycle,
                parent.execution_id,
                child_run_id=child_run_id,
                duration_seconds=time.monotonic() - self.started,
            )

    def close(self, *error: Any) -> None:
        try:
            self.scope.run(self.resources.__exit__, *error)
        finally:
            if error and error[0] is not None:
                with contextlib.suppress(Exception):
                    self.report_completion(
                        "cancelled"
                        if isinstance(error[1], (KeyboardInterrupt, SystemExit))
                        else "failed"
                    )


def record_migration_topology(ctx: MigrationExecutionContext, initial: MigrationGraphState) -> None:
    if ctx.store is not None:
        from refinery_ai.monitor.topology import graph_topology, save_topology

        graph = build_migration_graph(None)
        save_topology(
            ctx.store.state_root, initial["data"].run_id, graph_topology(graph, "migration")
        )


def run_migration_graph(runner: Any, request: MigrationRequest) -> MigrationRun:
    with migration_invocation(runner, request, standalone=True) as opened:
        if isinstance(opened, MigrationRun):
            return opened
        ctx, initial, checkpointer = opened
        graph = build_migration_graph(ctx, checkpointer=checkpointer)
        value = initial["data"]
        budgets = sum(max(0, stage.max_attempts) for stage in ctx.stages)
        config = {
            "configurable": {
                "thread_id": f"{value.run_id}:{value.generation}",
                "checkpoint_ns": "",
            },
            "recursion_limit": 20 + len(value.plan.steps) * (10 + 5 * budgets),
        }
        record_migration_topology(ctx, initial)
        result = graph.invoke(
            initial, config=config, **({"durability": "sync"} if ctx.store else {})
        )
        return MigrationRun.model_validate(result["result"])
