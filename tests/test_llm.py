from types import SimpleNamespace
from typing import Any

import pytest

from refinery_ai.config import Settings
from refinery_ai.llm import MaxTurnsExceeded, _litellm_completion, run_tool_loop, tool_definition


def response(*, content: str | None = None, tool_calls: list[Any] | None = None) -> Any:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def tool_call(name: str, arguments: str, call_id: str = "call-1") -> Any:
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def test_tool_definition_uses_signature_and_docstring() -> None:
    def lookup(component: str, limit: int = 5) -> list[str]:
        """Find matching components."""
        return []

    definition = tool_definition(lookup)["function"]
    assert definition["name"] == "lookup"
    assert definition["description"] == "Find matching components."
    assert definition["parameters"]["required"] == ["component"]
    assert definition["parameters"]["properties"]["limit"]["default"] == 5


def test_run_tool_loop_executes_tools_until_final_answer() -> None:
    requests: list[dict[str, Any]] = []

    def add(left: int, right: int) -> int:
        """Add two integers."""
        return left + right

    replies = iter(
        [
            response(tool_calls=[tool_call("add", '{"left": 2, "right": 3}')]),
            response(content="The answer is 5."),
        ]
    )

    def fake_completion(**kwargs: Any) -> Any:
        requests.append(kwargs)
        return next(replies)

    result = run_tool_loop(
        [{"role": "user", "prompt": "Add two and three"}],
        [add],
        max_turns=3,
        settings=Settings(
            litellm_model="ollama_chat/llama3.1:8b",
            litellm_api_base="http://ollama.test:11434",
        ),
        completion_fn=fake_completion,
    )

    assert result.content == "The answer is 5."
    assert result.turns == 2
    assert requests[0]["model"] == "ollama_chat/llama3.1:8b"
    assert requests[0]["api_base"] == "http://ollama.test:11434"
    assert requests[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "add",
        "content": "5",
    }


def test_run_tool_loop_passes_openai_key_without_an_api_base() -> None:
    requests: list[dict[str, Any]] = []

    def fake_completion(**kwargs: Any) -> Any:
        requests.append(kwargs)
        return response(content="Done")

    result = run_tool_loop(
        [{"role": "user", "prompt": "Plan it"}],
        settings=Settings(
            OPENAI_API_KEY="test-key",
            litellm_model="openai/gpt-5.6-terra",
            litellm_api_base=None,
        ),
        completion_fn=fake_completion,
    )

    assert result.content == "Done"
    assert requests[0]["model"] == "openai/gpt-5.6-terra"
    assert requests[0]["api_key"] == "test-key"
    assert "api_base" not in requests[0]


def test_run_tool_loop_reports_tool_errors_to_model() -> None:
    def fail() -> None:
        raise RuntimeError("broken")

    replies = iter(
        [response(tool_calls=[tool_call("fail", "{}")]), response(content="Recovered")]
    )
    requests: list[dict[str, Any]] = []

    def fake_completion(**kwargs: Any) -> Any:
        requests.append(kwargs)
        return next(replies)

    run_tool_loop(
        [{"role": "user", "prompt": "Try it"}], [fail], completion_fn=fake_completion
    )
    assert "RuntimeError: broken" in requests[1]["messages"][-1]["content"]


def test_run_tool_loop_enforces_max_turns() -> None:
    def again() -> str:
        return "again"

    with pytest.raises(MaxTurnsExceeded) as error:
        run_tool_loop(
            [{"role": "user", "prompt": "Loop"}],
            [again],
            max_turns=1,
            completion_fn=lambda **_: response(tool_calls=[tool_call("again", "{}")]),
        )
    assert error.value.messages[-1]["role"] == "assistant"


def test_run_tool_loop_validates_input() -> None:
    with pytest.raises(ValueError, match="max_turns"):
        run_tool_loop([], max_turns=0)


def test_real_completion_uses_configured_telemetry_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    callback = object()

    monkeypatch.setattr("refinery_ai.telemetry.litellm_callbacks", lambda: (callback,))
    import litellm

    monkeypatch.setattr(litellm, "callbacks", [])
    monkeypatch.setattr("litellm.completion", lambda **kwargs: requests.append(kwargs) or "ok")

    assert _litellm_completion(model="test", messages=[]) == "ok"
    assert litellm.callbacks == [callback]
    assert "success_callback" not in requests[0]
    assert "failure_callback" not in requests[0]


def test_real_completion_does_not_duplicate_global_telemetry_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = object()
    import litellm

    monkeypatch.setattr("refinery_ai.telemetry.litellm_callbacks", lambda: (callback,))
    monkeypatch.setattr(litellm, "callbacks", [callback])
    monkeypatch.setattr("litellm.completion", lambda **_: "ok")

    assert _litellm_completion(model="test", messages=[]) == "ok"
    assert litellm.callbacks == [callback]
