"""LangGraph boundary for recipe planning."""

from __future__ import annotations

import warnings
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from refinery_ai.agents.contracts import Planner, PlanningRequest
from refinery_ai.models import RecipePlan


class PlanningGraphState(TypedDict):
    request: PlanningRequest
    plan: NotRequired[RecipePlan]


def build_planning_graph(
    planner: Planner,
) -> CompiledStateGraph[PlanningGraphState, None, PlanningGraphState, PlanningGraphState]:
    """Build the in-process planning graph around one planner implementation."""

    def invoke_planner(state: PlanningGraphState) -> dict[str, RecipePlan]:
        return {"plan": planner.plan(state["request"])}

    def validate_plan(state: PlanningGraphState) -> dict[str, RecipePlan]:
        return {"plan": RecipePlan.model_validate(state["plan"])}

    graph = StateGraph(PlanningGraphState)
    graph.add_node("invoke_planner", invoke_planner)
    graph.add_node("validate_plan", validate_plan)
    graph.add_edge(START, "invoke_planner")
    graph.add_edge("invoke_planner", "validate_plan")
    graph.add_edge("validate_plan", END)
    # Planning is atomic inside the durable profile graph. Do not inherit its checkpointer:
    # PlanningRequest contains live catalog services rather than persistence state.
    return graph.compile(checkpointer=False)


def run_planning_graph(planner: Planner, request: PlanningRequest) -> RecipePlan:
    """Run one planning request through LangGraph and return its validated plan."""

    # The parent may use synchronous durability, but this graph has no checkpointer.
    # LangGraph warns about the override even though omitting it inherits "sync"
    # and crashes when the checkpoint-free child runs inside a durable parent.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"^`durability` has no effect when no checkpointer is present\.$",
            category=UserWarning,
            module=r"^langgraph\.pregel\.main$",
        )
        result = build_planning_graph(planner).invoke({"request": request}, durability="exit")
    return RecipePlan.model_validate(result["plan"])
