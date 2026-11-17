import pytest
from pydantic import ValidationError


def test_planner_response_schema_rejects_resolver_owned_objectives():
    from refinery_ai.agents.modernization import PlannerDestinationChoices

    with pytest.raises(ValidationError):
        PlannerDestinationChoices.model_validate(
            {
                "rationale": "Upgrade platform",
                "objectives": [
                    {
                        "id": "java-runtime-21",
                        "category": "compatibility",
                        "rationale": "Upgrade Java",
                        "evidence_ids": ["discovery"],
                        "completion": {"kind": "receipt"},
                    }
                ],
            }
        )


def test_planner_source_objective_survives_resolver_handoff(tmp_path, monkeypatch):
    from refinery_ai.agents.modernization import PlannerDestinationChoices
    from refinery_ai.modernization.resolver import DestinationChoices

    response = PlannerDestinationChoices.model_validate(
        {
            "spring_boot_version": "3.5.16",
            "rationale": "Upgrade platform and source",
            "objectives": [
                {
                    "id": "text-blocks",
                    "category": "source",
                    "rationale": "Simplify strings",
                    "evidence_ids": ["sonarqube"],
                    "completion": {
                        "kind": "finding",
                        "analyzer": "sonarqube",
                        "finding_ids": ["java:S6126"],
                    },
                }
            ],
        }
    )
    from refinery_ai.agents.modernization import LangGraphDestinationPlanner
    from refinery_ai.config import Settings
    from refinery_ai.models import BuildSystem, DiscoverySnapshot
    from refinery_ai.modernization.evidence import ProjectEvidence
    from refinery_ai.modernization.resolver import IntentResolutionRequest
    from refinery_ai.recipes.catalog import RecipeCatalogSession

    def create_agent(model, **kwargs):
        schema = kwargs["response_format"].schema
        invalid = response.model_dump()
        invalid["objectives"][0]["category"] = "compatibility"
        with pytest.raises(ValidationError):
            schema.model_validate(invalid)

        class Agent:
            def invoke(self, inputs, config):
                return {"structured_response": schema.model_validate(response.model_dump())}

        return Agent()

    monkeypatch.setattr("refinery_ai.agents.modernization.create_agent", create_agent)
    monkeypatch.setattr(
        "refinery_ai.agents.modernization._planning_model", lambda *a, **kw: object()
    )
    handoff = LangGraphDestinationPlanner(Settings(), RecipeCatalogSession([])).resolve(
        IntentResolutionRequest(
            evidence=ProjectEvidence(
                snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
                revision="abc",
                build_fingerprint="b",
                source_fingerprint="s",
            ),
            mode="full",
            requested_jdk=21,
            framework_candidates=[],
        )
    )
    assert isinstance(handoff, DestinationChoices)
    assert handoff.spring_boot_version == "3.5.16"
    assert handoff.objectives[0].category == "source"
    assert handoff.objectives[0].completion.finding_ids == ["java:S6126"]
