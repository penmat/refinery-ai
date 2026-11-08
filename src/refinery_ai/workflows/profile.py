"""LangGraph orchestration for profile-driven modernization loops."""

from __future__ import annotations

import contextlib
import hashlib
import time
from collections.abc import Callable
from sys import exc_info
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

from refinery_ai.migrations.execution_state import MigrationExecutionState, MigrationRequest
from refinery_ai.models import MigrationRun, RecipePlan, RunReport, RunStatus
from refinery_ai.monitor.migration_details import describe_migrations
from refinery_ai.monitor.models import AnalysisSummary
from refinery_ai.monitor.recording import (
    InvocationContext,
    ProfileReporter,
    capture_usage,
    parent_invocation,
)
from refinery_ai.monitor.topology import graph_topology, save_topology
from refinery_ai.profile_run import (
    ProfileAnalysis,
    ProfileRunResult,
    ProfileRunState,
    VerifiedCheckpoint,
    dependency_inputs_fingerprint,
)
from refinery_ai.profiles import EvidenceMode
from refinery_ai.workflows.migration import NestedMigration, build_migration_graph

if TYPE_CHECKING:
    from refinery_ai.profile_run import ProfileRunner


class ProfileGraphState(TypedDict):
    monitor_run_id: NotRequired[str]
    analysis_summary: NotRequired[AnalysisSummary | None]
    schema_version: int
    stored_state: ProfileRunState
    cycle: int
    previous_recipes: tuple[str, ...] | None
    verified_checkpoint: VerifiedCheckpoint | None
    report: NotRequired[RunReport]
    recipes: NotRequired[tuple[str, ...]]
    migration: NotRequired[MigrationRun]
    migration_request: NotRequired[MigrationRequest]
    migration_result: NotRequired[MigrationRun]
    interrupted_migration: NotRequired[MigrationExecutionState | None]
    result: NotRequired[ProfileRunResult]


def build_profile_graph(
    runner: ProfileRunner | None,
    *,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    reporter: ProfileReporter | None = None,
    nested: NestedMigration | None = None,
    legacy: bool = False,
) -> CompiledStateGraph[ProfileGraphState, None, ProfileGraphState, ProfileGraphState]:
    """Build the profile topology. Use run_profile_graph to manage native child resources."""

    if nested is None and not legacy and (runner is None or runner.migration_runner):
        nested = NestedMigration(None, reporter)

    def analyze(state: ProfileGraphState) -> dict[str, object]:
        assert runner is not None
        stored_state = state["stored_state"].model_copy(deep=True)
        cycle = state["cycle"] + 1
        dependency_fingerprint = dependency_inputs_fingerprint(runner.repository)
        sonar_enabled = runner.profile.evidence.sonarqube is not EvidenceMode.OFF
        run_sonar = sonar_enabled and (
            stored_state.sonar_dependency_fingerprint != dependency_fingerprint
        )
        sonar_note = (
            "without SonarQube"
            if not sonar_enabled
            else "with SonarQube"
            if run_sonar
            else "reusing SonarQube evidence"
        )
        runner.progress(f"Cycle {cycle}: analyzing ({sonar_note}) ...")
        result = runner.analyze(run_sonar)
        sonar_available = result.sonar_available if isinstance(result, ProfileAnalysis) else result
        if sonar_enabled and not run_sonar and not sonar_available:
            runner.progress("Retained SonarQube evidence is unavailable; running a fresh scan ...")
            run_sonar = True
            result = runner.analyze(True)
            sonar_available = (
                result.sonar_available if isinstance(result, ProfileAnalysis) else result
            )
        if run_sonar and sonar_available:
            stored_state.sonar_dependency_fingerprint = dependency_fingerprint
        stored_state.cycles += 1
        stored_state.completed = False
        runner.store.save(stored_state)
        return {
            "stored_state": stored_state,
            "cycle": cycle,
            "analysis_summary": result.summary if isinstance(result, ProfileAnalysis) else None,
        }

    def plan(state: ProfileGraphState) -> dict[str, object]:
        assert runner is not None
        cycle = state["cycle"]
        runner.progress(f"Cycle {cycle}: planning for {runner.profile.name} ...")
        report = runner.plan()
        if not isinstance(report.plan, RecipePlan):
            raise RuntimeError("Profile planner did not return recipe recommendations")
        recipes = tuple(item.recipe for item in report.plan.recommendations)
        update: dict[str, object] = {"report": report, "recipes": recipes}
        if not recipes:
            target_gaps = runner.profile.unresolved_targets(report.snapshot)
            if target_gaps:
                unresolved = ", ".join(
                    f"{gap.target} {gap.current_version or 'unknown'} -> {gap.target_version}"
                    for gap in target_gaps
                )
                update["result"] = runner._stalled(
                    state["stored_state"],
                    cycle,
                    "the planner returned no migrations while profile targets remain "
                    f"unsatisfied ({unresolved})",
                )
                return update
            stored_state = state["stored_state"].model_copy(deep=True)
            stored_state.completed = True
            runner.store.save(stored_state)
            update["stored_state"] = stored_state
            update["result"] = ProfileRunResult(
                profile=runner.profile.name,
                status=RunStatus.COMPLETED,
                cycles=cycle,
                message="Profile complete; the planner found no remaining migrations.",
            )
            return update
        if recipes == state["previous_recipes"]:
            update["result"] = runner._stalled(
                state["stored_state"], cycle, "the planner repeated the same migration plan"
            )
        if nested is not None and "result" not in update:
            checkpoint = state["verified_checkpoint"]
            runner.progress(f"Cycle {cycle}: running {len(recipes)} migration(s) ...")
            update["interrupted_migration"] = None
            update["migration_request"] = MigrationRequest(
                plan_id=report.run_id,
                apply=True,
                publish=True,
                expected_profile=runner.profile.name,
                trusted_baseline_revision=checkpoint.revision if checkpoint else None,
                trusted_baseline_java=checkpoint.java if checkpoint else None,
                resume_bound_run=True,
            )
        return update

    def after_plan(state: ProfileGraphState) -> Literal["migrate", "finished"]:
        return "finished" if "result" in state else "migrate"

    def migrate(state: ProfileGraphState) -> dict[str, object]:
        assert runner is not None
        recipes = state["recipes"]
        report = state["report"]
        cycle = state["cycle"]
        runner.progress(f"Cycle {cycle}: running {len(recipes)} migration(s) ...")
        migration = runner.migrate(report.run_id, state["verified_checkpoint"])
        return apply_migration_result(state, migration)

    def apply_migration_result(
        state: ProfileGraphState, migration: MigrationRun
    ) -> dict[str, object]:
        assert runner is not None
        cycle = state["cycle"]
        update: dict[str, object] = {
            "migration": migration,
            "previous_recipes": state["recipes"],
        }
        if migration.status is not RunStatus.COMPLETED:
            update["result"] = ProfileRunResult(
                profile=runner.profile.name,
                status=migration.status,
                cycles=cycle,
                message="Profile run stopped because a migration failed.",
            )
            return update
        if migration.published_revision is None:
            runner.progress(
                "The migration produced no published changes; rechecking profile targets ..."
            )
        update["verified_checkpoint"] = (
            VerifiedCheckpoint(
                revision=migration.published_revision,
                java=migration.verified_java,
            )
            if migration.published_revision is not None and migration.verified_java is not None
            else None
        )
        if cycle >= runner.max_cycles:
            update["result"] = runner._stalled(
                state["stored_state"], cycle, "the maximum cycle count was reached"
            )
        return update

    def after_migrate(state: ProfileGraphState) -> Literal["analyze", "finished"]:
        return "finished" if "result" in state else "analyze"

    def observed(name: str, operation: Callable[[ProfileGraphState], dict[str, object]]) -> Any:
        def invoke(state: ProfileGraphState) -> dict[str, object]:
            if reporter is None:
                return operation(state)
            execution_id = uuid4().hex
            started = time.monotonic()
            reporter.emit(name, "started", execution_id)
            try:
                with (
                    parent_invocation(InvocationContext(reporter.run_id, execution_id)),
                    capture_usage(
                        lambda input_tokens, output_tokens, model, cost: reporter.emit(
                            name,
                            "usage",
                            execution_id,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            model=model,
                            estimated_cost_usd=cost,
                        )
                    ),
                ):
                    update = operation(state)
                report = update.get("report")
                migrations = (
                    describe_migrations(item.recipe for item in report.plan.recommendations)
                    if name == "plan"
                    and isinstance(report, RunReport)
                    and isinstance(report.plan, RecipePlan)
                    else None
                )
                analysis = update.get("analysis_summary")
                reporter.emit(
                    name,
                    "completed",
                    execution_id,
                    duration_seconds=time.monotonic() - started,
                    migrations=migrations,
                    analysis=analysis if isinstance(analysis, AnalysisSummary) else None,
                )
                return update
            except BaseException as error:
                with contextlib.suppress(Exception):
                    reporter.emit(
                        name,
                        "cancelled"
                        if isinstance(error, (KeyboardInterrupt, SystemExit))
                        else "failed",
                        execution_id,
                        duration_seconds=time.monotonic() - started,
                    )
                raise

        return invoke

    graph = StateGraph(ProfileGraphState)
    graph.add_node("analyze", observed("analyze", analyze))
    graph.add_node("plan", observed("plan", plan))
    if nested is None:
        graph.add_node("migrate", observed("migrate", migrate))
        migration_exit = "migrate"
    else:
        graph.add_node("migrate", build_migration_graph(None, nested=nested))

        def accept_migration(state: ProfileGraphState) -> dict[str, object]:
            return apply_migration_result(state, state["migration_result"])

        migration_exit = "accept_migration"
        graph.add_node(migration_exit, observed(migration_exit, accept_migration))
        graph.add_edge("migrate", migration_exit)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "plan")
    graph.add_conditional_edges("plan", after_plan, {"migrate": "migrate", "finished": END})
    graph.add_conditional_edges(
        migration_exit, after_migrate, {"analyze": "analyze", "finished": END}
    )
    return graph.compile(checkpointer=checkpointer)


def profile_thread_id(runner: ProfileRunner) -> str:
    """Return the stable checkpoint identity for one repository profile."""

    repository_id = hashlib.sha256(str(runner.repository).encode()).hexdigest()[:16]
    return f"profile-v1:{repository_id}:{runner.profile.name}"


def run_profile_graph(runner: ProfileRunner) -> ProfileRunResult:
    """Run a profile until completion, failure, or a deterministic stop condition."""

    checkpoint_path = runner.store.path.parents[1] / "langgraph-checkpoints.sqlite"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    config: RunnableConfig = {
        "configurable": {"thread_id": profile_thread_id(runner)},
        "recursion_limit": runner.max_cycles * 3 + 5,
    }
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        graph = build_profile_graph(runner, checkpointer=checkpointer)
        snapshot = graph.get_state(config)
        graph_input: ProfileGraphState | None
        if snapshot.next:
            graph_input = None
        else:
            graph_input = {
                "monitor_run_id": uuid4().hex,
                "schema_version": 2 if runner.migration_runner else 1,
                "stored_state": runner.store.load(),
                "cycle": 0,
                "previous_recipes": None,
                "verified_checkpoint": None,
            }
        monitor_id = (
            snapshot.values.get("monitor_run_id")
            if graph_input is None
            else graph_input["monitor_run_id"]
        ) or uuid4().hex
        if graph_input is None and not snapshot.values.get("monitor_run_id"):
            graph.update_state(config, {"monitor_run_id": monitor_id})
        state_root = runner.store.path.parents[1]
        reporter = ProfileReporter(state_root, monitor_id, runner.profile.name)
        legacy = graph_input is None and snapshot.values.get("schema_version", 1) == 1
        nested = (
            NestedMigration(runner.migration_runner, reporter)
            if runner.migration_runner and not legacy
            else None
        )
        graph = build_profile_graph(
            runner, checkpointer=checkpointer, reporter=reporter, nested=nested, legacy=legacy
        )
        try:
            save_topology(state_root, monitor_id, graph_topology(graph, "profile"))
            pending = {e.execution_id: e for e in reporter.history if e.lifecycle == "started"}
            for event in reporter.history:
                if event.lifecycle not in ("started", "usage"):
                    pending.pop(event.execution_id, None)
            for event in pending.values():
                reporter.emit(event.phase, "interrupted", event.execution_id)
            if nested is not None:
                # LangGraph passes this budget to the migration subgraph as well.
                config["recursion_limit"] = max(10_000, runner.max_cycles * 4 + 5)
                if graph_input is None and "migrate" in snapshot.next:
                    # Schedule a fresh child task. The migration reconciles Git receipts
                    # under its lease before continuing from a verified recipe boundary.
                    expanded = graph.get_state(config, subgraphs=True)
                    nested.saved = snapshot.values.get("interrupted_migration")
                    for task in expanded.tasks:
                        if task.name == "migrate" and isinstance(task.state, StateSnapshot):
                            nested.saved = task.state.values.get("data") or nested.saved
                    graph.update_state(
                        config,
                        {"interrupted_migration": nested.saved},
                        as_node="plan",
                    )
            result = graph.invoke(graph_input, config=config, durability="sync")
            reporter.emit(
                "run", "completed", uuid4().hex, outcome=str(result["result"].status.value)
            )
        finally:
            try:
                if nested is not None:
                    nested.close(*exc_info())
            finally:
                reporter.close()
    return ProfileRunResult.model_validate(result["result"])
