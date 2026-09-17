"""LangGraph orchestration for profile-driven modernization loops."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from refinery_ai.models import MigrationRun, RecipePlan, RunReport, RunStatus
from refinery_ai.profile_run import (
    ProfileRunResult,
    ProfileRunState,
    VerifiedCheckpoint,
    dependency_inputs_fingerprint,
)
from refinery_ai.profiles import EvidenceMode

if TYPE_CHECKING:
    from refinery_ai.profile_run import ProfileRunner


class ProfileGraphState(TypedDict):
    stored_state: ProfileRunState
    cycle: int
    previous_recipes: tuple[str, ...] | None
    verified_checkpoint: VerifiedCheckpoint | None
    report: NotRequired[RunReport]
    recipes: NotRequired[tuple[str, ...]]
    migration: NotRequired[MigrationRun]
    result: NotRequired[ProfileRunResult]


def build_profile_graph(
    runner: ProfileRunner,
) -> CompiledStateGraph[ProfileGraphState, None, ProfileGraphState, ProfileGraphState]:
    """Build the deterministic profile loop as explicit LangGraph nodes."""

    def analyze(state: ProfileGraphState) -> dict[str, object]:
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
        sonar_available = runner.analyze(run_sonar)
        if sonar_enabled and not run_sonar and not sonar_available:
            runner.progress("Retained SonarQube evidence is unavailable; running a fresh scan ...")
            run_sonar = True
            sonar_available = runner.analyze(True)
        if run_sonar and sonar_available:
            stored_state.sonar_dependency_fingerprint = dependency_fingerprint
        stored_state.cycles += 1
        stored_state.completed = False
        runner.store.save(stored_state)
        return {"stored_state": stored_state, "cycle": cycle}

    def plan(state: ProfileGraphState) -> dict[str, object]:
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
        return update

    def after_plan(state: ProfileGraphState) -> Literal["migrate", "finished"]:
        return "finished" if "result" in state else "migrate"

    def migrate(state: ProfileGraphState) -> dict[str, object]:
        recipes = state["recipes"]
        report = state["report"]
        cycle = state["cycle"]
        runner.progress(f"Cycle {cycle}: running {len(recipes)} migration(s) ...")
        migration = runner.migrate(report.run_id, state["verified_checkpoint"])
        update: dict[str, object] = {
            "migration": migration,
            "previous_recipes": recipes,
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

    graph = StateGraph(ProfileGraphState)
    graph.add_node("analyze", analyze)
    graph.add_node("plan", plan)
    graph.add_node("migrate", migrate)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "plan")
    graph.add_conditional_edges("plan", after_plan, {"migrate": "migrate", "finished": END})
    graph.add_conditional_edges("migrate", after_migrate, {"analyze": "analyze", "finished": END})
    return graph.compile()


def run_profile_graph(runner: ProfileRunner) -> ProfileRunResult:
    """Run a profile until completion, failure, or a deterministic stop condition."""

    result = build_profile_graph(runner).invoke(
        {
            "stored_state": runner.store.load(),
            "cycle": 0,
            "previous_recipes": None,
            "verified_checkpoint": None,
        },
        config={"recursion_limit": runner.max_cycles * 3 + 5},
    )
    return ProfileRunResult.model_validate(result["result"])
