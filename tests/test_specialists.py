from pathlib import Path

from refinery_ai.harness import create_read_only_assessment_harness
from refinery_ai.specialists import (
    BuildRuntimeSpecialistResult,
    SpecialistObservation,
    merge_specialist_results,
)


class FakeSpecialistGraph:
    def invoke(self, input, config=None):
        del input, config
        return {
            "structured_response": {
                "observations": [
                    {
                        "key": "build_system",
                        "value": "maven",
                        "evidence_paths": ["pom.xml"],
                    }
                ],
                "unresolved_questions": [],
            }
        }


class FakeParentGraph:
    pass


def test_parent_has_bounded_typed_build_runtime_delegation(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_create_agent(*args, **kwargs):
        calls.append(kwargs)
        return FakeSpecialistGraph() if len(calls) == 1 else FakeParentGraph()

    monkeypatch.setattr("refinery_ai.harness.create_agent", fake_create_agent)
    bundle = create_read_only_assessment_harness("test:model", tmp_path)
    delegation = next(
        tool for tool in calls[1]["tools"] if tool.__name__ == "investigate_build_runtime"
    )

    result = delegation("Identify the build system")

    assert isinstance(result, BuildRuntimeSpecialistResult)
    assert result.observations[0].evidence_paths == ("pom.xml",)
    assert bundle.agent.__class__ is FakeParentGraph


def test_specialist_merge_deduplicates_and_is_order_stable() -> None:
    first = BuildRuntimeSpecialistResult(
        observations=(
            SpecialistObservation(key="runtime", value="17", evidence_paths=("pom.xml",)),
        ),
        unresolved_questions=("Who owns deployment?",),
    )
    second = BuildRuntimeSpecialistResult(
        observations=(
            SpecialistObservation(key="runtime", value="17", evidence_paths=("README.md",)),
        ),
        unresolved_questions=("Who owns deployment?",),
    )

    forward = merge_specialist_results([first, second])
    reverse = merge_specialist_results([second, first])

    assert forward == reverse
    assert forward.observations[0].evidence_paths == ("README.md", "pom.xml")
    assert forward.unresolved_questions == ("Who owns deployment?",)


def test_specialist_merge_preserves_conflicts() -> None:
    results = [
        BuildRuntimeSpecialistResult(
            observations=(SpecialistObservation(key="runtime", value="11"),)
        ),
        BuildRuntimeSpecialistResult(
            observations=(SpecialistObservation(key="runtime", value="17"),)
        ),
    ]

    merged = merge_specialist_results(results)

    assert not merged.observations
    assert merged.conflicts[0].key == "runtime"
    assert merged.conflicts[0].values == ("11", "17")
