import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from refinery_ai.cli import app
from refinery_ai.monitor.cli import serve_monitor


def test_command_options_and_defaults(tmp_path, monkeypatch):
    from refinery_ai.monitor import cli

    received = []
    monkeypatch.setattr(cli, "serve_monitor", lambda *args: received.append(args))
    result = CliRunner().invoke(
        app, ["monitor", "--repository", str(tmp_path), "--port", "8888", "--no-browser"]
    )
    assert result.exit_code == 0, result.output
    assert received[0][0] == tmp_path
    assert received[0][2:] == (8888, False)


def test_invalid_port_and_repository_are_cli_errors(tmp_path):
    for args in [["--port", "0"], ["--port", "65536"], ["--repository", str(tmp_path / "missing")]]:
        result = CliRunner().invoke(app, ["monitor", *args])
        assert result.exit_code == 2


def test_occupied_port_has_clear_error_and_no_journal_writes(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(OSError, match="already in use"):
            serve_monitor(tmp_path, Path(".refinery"), port, False)
    assert not (tmp_path / ".refinery").exists()
