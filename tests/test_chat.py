from pathlib import Path
from types import SimpleNamespace
from typing import Any

from typer.testing import CliRunner

from refinery_ai.cli import app


def test_chat_includes_reports_tools_and_conversation_history(
    tmp_path: Path, monkeypatch: Any
) -> None:
    refinery = tmp_path / ".refinery"
    refinery.mkdir()
    (refinery / "summary.md").write_text("# Summary\n\nA Spring service.")
    (refinery / "assessment.md").write_text("Modernization Score: 82/100")
    calls: list[dict[str, Any]] = []

    def fake_loop(messages: Any, tools: Any, max_turns: int, settings: Any) -> Any:
        calls.append(
            {
                "messages": list(messages),
                "tool_names": [tool.__name__ for tool in tools],
                "max_turns": max_turns,
                "model": settings.planner_model,
            }
        )
        answer = "It is a Spring application." if len(calls) == 1 else "It uses Java."
        return SimpleNamespace(content=answer)

    monkeypatch.setattr("refinery_ai.cli.run_tool_loop", fake_loop)
    result = CliRunner().invoke(
        app,
        ["chat", str(tmp_path), "--max-turns", "3"],
        input="What is this?\nWhat language?\nexit\n",
    )

    assert result.exit_code == 0
    assert result.stdout.startswith(
        "Welcome to Refinery AI: secure, local, deterministic!\n\n"
        "Ask me a question about your project ..."
    )
    assert "Refinery AI: It is a Spring application." in result.stdout
    assert "Refinery AI: It uses Java." in result.stdout
    assert "# Summary" in calls[0]["messages"][0]["prompt"]
    assert "Modernization Score: 82/100" in calls[0]["messages"][0]["prompt"]
    assert calls[0]["tool_names"] == [
        "get_repository_overview",
        "list_symbols",
        "find_symbol",
        "explain_symbol",
        "get_callers",
        "get_callees",
        "get_dependencies",
        "get_dependents",
        "search_findings",
        "get_inspector_result",
    ]
    assert calls[0]["max_turns"] == 3
    assert calls[0]["model"] == "openai:gpt-5.6-luna"
    assert calls[1]["messages"][-2:] == [
        {"role": "assistant", "prompt": "It is a Spring application."},
        {"role": "user", "prompt": "What language?"},
    ]


def test_chat_handles_missing_reports_and_provider_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        messages = args[0]
        assert "No retained summary.md or assessment.md" in messages[0]["prompt"]
        raise RuntimeError("Ollama is unavailable")

    monkeypatch.setattr("refinery_ai.cli.run_tool_loop", fail)
    result = CliRunner().invoke(
        app, ["chat", str(tmp_path)], input="Hello\nquit\n"
    )

    assert result.exit_code == 0
    assert "Unable to answer: Ollama is unavailable" in result.output


def test_chat_exits_cleanly_on_end_of_input(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["chat", str(tmp_path)], input="")

    assert result.exit_code == 0
    assert result.stdout.startswith("Welcome to Refinery AI")
