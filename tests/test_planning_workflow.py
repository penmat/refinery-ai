import warnings
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from refinery_ai.agents.contracts import PlanningDependencies, PlanningRequest
from refinery_ai.models import BuildSystem, DiscoverySnapshot, RecipePlan, RecipeRecommendation
from refinery_ai.workflows.planning import build_planning_graph, run_planning_graph


class RecordingPlanner:
    def __init__(self, output: RecipePlan) -> None:
        self.output = output
        self.requests: list[PlanningRequest] = []

    def plan(self, request: PlanningRequest) -> RecipePlan:
        self.requests.append(request)
        return self.output


def request() -> PlanningRequest:
    return PlanningRequest(
        prompt="Plan the migration",
        dependencies=PlanningDependencies(
            snapshot=DiscoverySnapshot(
                repository=Path("/repository"),
                build_system=BuildSystem.MAVEN,
            ),
            graph_summary="empty graph",
        ),
        run_id="run1",
        plan_id="plan1",
    )


def test_planning_graph_invokes_planner_and_validates_output() -> None:
    expected = RecipePlan(
        recommendations=[
            RecipeRecommendation(
                recipe="example.Upgrade",
                reason="Required",
                evidence=["pom.xml"],
                expected_benefit="Upgrade",
                priority="high",
                confidence=1,
            )
        ]
    )
    planner = RecordingPlanner(expected)
    planning_request = request()

    assert run_planning_graph(planner, planning_request) == expected
    assert planner.requests == [planning_request]


def test_planning_graph_has_explicit_invocation_and_validation_nodes() -> None:
    graph = build_planning_graph(RecordingPlanner(RecipePlan()))

    assert set(graph.get_graph().nodes) == {
        "__start__",
        "invoke_planner",
        "validate_plan",
        "__end__",
    }


def test_planning_graph_does_not_inherit_parent_sqlite_checkpointer(tmp_path: Path) -> None:
    class ParentState(TypedDict):
        planned: bool

    planner = RecordingPlanner(RecipePlan())

    def plan_node(state: ParentState) -> dict[str, bool]:
        del state
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            run_planning_graph(planner, request())
        return {"planned": True}

    builder = StateGraph(ParentState)
    builder.add_node("plan", plan_node)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", END)
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as checkpointer:
        graph = builder.compile(checkpointer=checkpointer)
        result = graph.invoke(
            {"planned": False},
            config={"configurable": {"thread_id": "nested-planning"}},
            durability="sync",
        )

    assert result["planned"] is True
    assert len(planner.requests) == 1
