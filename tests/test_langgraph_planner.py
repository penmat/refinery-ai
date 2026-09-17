from pathlib import Path
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel

from refinery_ai.agents.contracts import PlanningDependencies, PlanningRequest
from refinery_ai.agents.planning import LangGraphPlanner
from refinery_ai.models import BuildSystem, DiscoverySnapshot, RecipePlan, RecipeRecommendation
from refinery_ai.recipes.catalog import RecipeCatalogSession
from refinery_ai.recipes.sources import RecipeSource


def test_langgraph_planner_uses_catalog_tools_and_deterministic_grounding(monkeypatch) -> None:
    source = RecipeSource(
        recipe="example.Upgrade",
        display_name="Upgrade",
        description="Upgrade the example project.",
        repository="example/recipes",
        file_path="recipes.yml",
    )
    catalog = RecipeCatalogSession([source])
    dependencies = PlanningDependencies(
        snapshot=DiscoverySnapshot(
            repository=Path("/repository"), build_system=BuildSystem.MAVEN
        ),
        graph_summary="empty",
        evidence_corpus="discovery.java_version=11",
        recipe_catalog=catalog,
    )
    recommendation = RecipeRecommendation(
        recipe=source.recipe,
        reason="Required",
        evidence=["discovery.java_version=11"],
        expected_benefit="Upgrade",
        priority="high",
        confidence=1,
    )
    captured: dict[str, Any] = {}

    class FakeAgent:
        def invoke(self, inputs, config):
            captured["inputs"] = inputs
            captured["config"] = config
            captured["tools"][2](source.recipe)
            return {
                "messages": [],
                "structured_response": RecipePlan(recommendations=[recommendation]),
            }

    def fake_create_agent(model, tools, **kwargs):
        captured.update(model=model, tools=tools, kwargs=kwargs)
        return FakeAgent()

    monkeypatch.setattr("refinery_ai.agents.planning.create_agent", fake_create_agent)
    planner = LangGraphPlanner(cast(BaseChatModel, object()), "test-model")

    result = planner.plan(
        PlanningRequest(
            prompt="Plan",
            dependencies=dependencies,
            run_id="run1",
            plan_id="plan1",
        )
    )

    assert result.recommendations == [
        recommendation.model_copy(
            update={"recipe_artifact": "org.openrewrite.recipe:recipes:RELEASE"}
        )
    ]
    assert captured["config"]["metadata"]["refinery.run.id"] == "run1"
    assert captured["kwargs"]["name"] == "refinery-planner"
