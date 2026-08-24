from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from refinery_ai.harness import create_read_only_assessment_harness
from refinery_ai.specialists import (
    BuildRuntimeSpecialistResult,
    SpecialistObservation,
    merge_specialist_results,
)


@pytest.mark.asyncio
async def test_build_runtime_specialist_returns_typed_result(tmp_path: Path) -> None:
    model = TestModel(
        call_tools=[],
        custom_output_args={
            "observations": [
                {"key": "build_system", "value": "maven", "evidence_paths": ["pom.xml"]}
            ],
            "unresolved_questions": [],
        },
    )
    bundle = create_read_only_assessment_harness(model, tmp_path)

    result = (await bundle.build_runtime_specialist.run("Identify the build system")).output

    assert isinstance(result, BuildRuntimeSpecialistResult)
    assert result.observations[0].evidence_paths == ("pom.xml",)


@pytest.mark.asyncio
async def test_parent_delegates_to_build_runtime_specialist_through_subagents(
    tmp_path: Path,
) -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tools = {tool.name for tool in info.function_tools}
        if "delegate_task" in tools:
            delegated = any(
                isinstance(part, ToolReturnPart) for message in messages for part in message.parts
            )
            if delegated:
                return ModelResponse(parts=[TextPart("delegation complete")])
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {
                            "agent_name": "build-runtime-specialist",
                            "task": "Identify the build system with evidence",
                        },
                    )
                ]
            )
        output_tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "observations": [
                            {
                                "key": "build_system",
                                "value": "maven",
                                "evidence_paths": ["pom.xml"],
                            }
                        ],
                        "unresolved_questions": [],
                    },
                )
            ]
        )

    bundle = create_read_only_assessment_harness(FunctionModel(respond), tmp_path)

    result = await bundle.agent.run("Delegate the build/runtime investigation")

    assert result.output == "delegation complete"
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_name == "delegate_task"
        for message in result.all_messages()
        for part in message.parts
    )


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
