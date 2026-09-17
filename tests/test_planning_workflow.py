from pathlib import Path

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
