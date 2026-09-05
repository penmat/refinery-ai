"""Provider-neutral LLM calls and a bounded Python tool loop."""

from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, get_type_hints

from pydantic import TypeAdapter

from refinery_ai.config import Settings
from refinery_ai.telemetry import record_llm_usage

Message = Mapping[str, Any]
Tool = Callable[..., Any]


class CompletionFunction(Protocol):
    """The subset of LiteLLM's completion API used by the tool loop."""

    def __call__(self, **kwargs: Any) -> Any: ...


def _litellm_completion(**kwargs: Any) -> Any:
    """Import LiteLLM only when making a real provider request."""

    litellm: Any = importlib.import_module("litellm")
    from refinery_ai.telemetry import litellm_callbacks

    callbacks = litellm_callbacks()
    if callbacks:
        configured_callbacks = list(getattr(litellm, "callbacks", None) or [])
        for callback in callbacks:
            if callback not in configured_callbacks:
                configured_callbacks.append(callback)
        litellm.callbacks = configured_callbacks
    return litellm.completion(**kwargs)


@dataclass(frozen=True)
class ToolLoopResult:
    """Final answer and the full message history produced by a tool loop."""

    content: str | None
    messages: tuple[dict[str, Any], ...]
    turns: int
    response: Any


class MaxTurnsExceeded(RuntimeError):
    """Raised when the model keeps requesting tools after the configured limit."""

    def __init__(self, max_turns: int, messages: Sequence[dict[str, Any]]) -> None:
        super().__init__(f"LLM tool loop exceeded its limit of {max_turns} turns")
        self.messages = tuple(messages)


def _json_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty:
        return {}
    return TypeAdapter(annotation).json_schema()


def tool_definition(tool: Tool) -> dict[str, Any]:
    """Translate a typed Python callable into an OpenAI-compatible tool definition."""

    signature = inspect.signature(tool)
    try:
        hints = get_type_hints(tool, include_extras=True)
    except (NameError, TypeError):
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise TypeError(f"Tool {tool.__name__!r} cannot use *args or **kwargs")
        properties[name] = _json_schema(hints.get(name, parameter.annotation))
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        else:
            properties[name]["default"] = parameter.default

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {
            "name": tool.__name__,
            "description": inspect.getdoc(tool) or "",
            "parameters": parameters,
        },
    }


def _message_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _arguments(tool_call: Any) -> dict[str, Any]:
    function = _message_value(tool_call, "function")
    raw = _message_value(function, "arguments", {})
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(parsed, dict):
        raise TypeError("Tool arguments must decode to a JSON object")
    return parsed


def _json_result(result: Any) -> str:
    try:
        return json.dumps(result)
    except TypeError:
        return json.dumps(str(result))


def run_tool_loop(
    messages: Sequence[Message],
    tools: Sequence[Tool] = (),
    max_turns: int = 8,
    *,
    settings: Settings | None = None,
    completion_fn: CompletionFunction = _litellm_completion,
) -> ToolLoopResult:
    """Call LiteLLM until the model answers or the bounded tool loop is exhausted.

    Input messages may use ``prompt`` as requested by the application boundary, or
    the provider-standard ``content`` key. Tools are ordinary typed Python callables.
    """

    if max_turns < 1:
        raise ValueError("max_turns must be at least 1")

    config = settings or Settings()
    history: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("prompt", message.get("content"))
        if not isinstance(role, str) or content is None:
            raise ValueError("Each message must contain a role and prompt (or content)")
        history.append({"role": role, "content": content})

    tool_map = {tool.__name__: tool for tool in tools}
    if len(tool_map) != len(tools):
        raise ValueError("Tool names must be unique")
    definitions = [tool_definition(tool) for tool in tools]

    last_response: Any = None
    for turn in range(1, max_turns + 1):
        request: dict[str, Any] = {
            "model": config.litellm_model,
            "messages": list(history),
        }
        if config.litellm_api_base:
            request["api_base"] = config.litellm_api_base
        if config.openai_api_key and config.litellm_model.startswith("openai/"):
            request["api_key"] = config.openai_api_key.get_secret_value()
        if definitions:
            request["tools"] = definitions
        last_response = completion_fn(**request)
        record_llm_usage(
            _message_value(last_response, "usage"),
            model=_message_value(last_response, "model", config.litellm_model),
        )
        response_message = last_response.choices[0].message
        content = _message_value(response_message, "content")
        tool_calls = list(_message_value(response_message, "tool_calls") or [])

        assistant: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            assistant["tool_calls"] = []
            for index, tool_call in enumerate(tool_calls):
                function = _message_value(tool_call, "function")
                arguments = _message_value(function, "arguments", "{}")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments)
                assistant["tool_calls"].append(
                    {
                        "id": _message_value(tool_call, "id") or f"call_{turn}_{index}",
                        "type": "function",
                        "function": {
                            "name": _message_value(function, "name"),
                            "arguments": arguments,
                        },
                    }
                )
        history.append(assistant)

        if not tool_calls:
            return ToolLoopResult(content, tuple(history), turn, last_response)

        if turn == max_turns:
            break
        for index, tool_call in enumerate(tool_calls):
            function = _message_value(tool_call, "function")
            name = _message_value(function, "name")
            call_id = _message_value(tool_call, "id") or f"call_{turn}_{index}"
            tool = tool_map.get(name)
            if tool is None:
                result: Any = {"error": f"Unknown tool: {name}"}
            else:
                try:
                    result = tool(**_arguments(tool_call))
                except Exception as exc:  # Tool failures are context for the model to recover from.
                    result = {"error": f"{type(exc).__name__}: {exc}"}
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": _json_result(result),
                }
            )

    raise MaxTurnsExceeded(max_turns, history)
