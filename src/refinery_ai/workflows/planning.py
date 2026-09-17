"""LangGraph boundary for recipe planning."""

from __future__ import annotations

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
    return graph.compile()


def run_planning_graph(planner: Planner, request: PlanningRequest) -> RecipePlan:
    """Run one planning request through LangGraph and return its validated plan."""

    result = build_planning_graph(planner).invoke({"request": request})
    return RecipePlan.model_validate(result["plan"])
