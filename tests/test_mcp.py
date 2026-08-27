from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from refinery_ai.cli import app
from refinery_ai.mcp import create_mcp_server, run_mcp_server


def test_mcp_server_exposes_complete_evidence_catalog(tmp_path: Path) -> None:
    server = create_mcp_server(tmp_path)

    tools = server._tool_manager.list_tools()

    assert [tool.name for tool in tools] == [
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
    assert all(tool.description for tool in tools)
    assert (
        tools[4].parameters["properties"]["symbol_id"]["description"].startswith("Exact symbol ID")
    )


def test_mcp_command_launches_server_for_resolved_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_server = Mock()
    monkeypatch.setattr("refinery_ai.cli.run_mcp_server", run_server)

    result = CliRunner().invoke(app, ["mcp", str(tmp_path)])

    assert result.exit_code == 0
    run_server.assert_called_once_with(
        tmp_path.resolve(), transport="stdio", host="127.0.0.1", port=8000
    )


def test_mcp_command_configures_http_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_server = Mock()
    monkeypatch.setattr("refinery_ai.cli.run_mcp_server", run_server)

    result = CliRunner().invoke(
        app,
        [
            "mcp",
            str(tmp_path),
            "--transport",
            "http",
            "--host",
            "0.0.0.0",
            "--port",
            "8765",
        ],
    )

    assert result.exit_code == 0
    run_server.assert_called_once_with(
        tmp_path.resolve(), transport="http", host="0.0.0.0", port=8765
    )


def test_http_transport_runs_streamable_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = Mock()
    create_server = Mock(return_value=server)
    monkeypatch.setattr("refinery_ai.mcp.create_mcp_server", create_server)

    run_mcp_server(tmp_path, transport="http", host="localhost", port=9000)

    create_server.assert_called_once_with(tmp_path, host="localhost", port=9000)
    server.run.assert_called_once_with(transport="streamable-http")
