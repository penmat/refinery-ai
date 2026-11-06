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


def test_monitor_does_not_configure_telemetry(monkeypatch):
    from refinery_ai import cli
    from refinery_ai.monitor import cli as monitor_cli

    monkeypatch.setenv("REFINERY_OTEL_ENABLED", "true")
    monkeypatch.setattr(monitor_cli, "serve_monitor", lambda *args: None)

    def unexpected_export(settings):
        pytest.fail("The local monitor must not initialize telemetry")

    monkeypatch.setattr(cli, "configure_telemetry", unexpected_export)
    result = CliRunner().invoke(app, ["monitor", "--no-browser"])
    assert result.exit_code == 0, result.exception


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


def test_ctrl_c_closes_an_open_event_stream(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time
    from urllib.error import URLError
    from urllib.request import urlopen

    from test_monitor_journals import journal

    if os.name != "posix":
        pytest.skip("POSIX signal test")
    journal(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = (
        "from pathlib import Path; from refinery_ai.monitor.cli import serve_monitor; "
        'import sys; serve_monitor(Path(sys.argv[1]),Path(".refinery"),int(sys.argv[2]),False)'
    )
    process = subprocess.Popen(
        [sys.executable, "-c", command, str(tmp_path), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    connection = None
    try:
        for _ in range(100):
            try:
                connection = urlopen(
                    f"http://127.0.0.1:{port}/api/runs/" + "a" * 32 + "/events", timeout=1
                )
                break
            except URLError:
                time.sleep(0.05)
        assert connection is not None
        assert connection.readline() == b"id: 1\n"
        process.send_signal(signal.SIGINT)
        process.wait(timeout=4)
    finally:
        if connection is not None:
            connection.close()
        if process.poll() is None:
            process.kill()
            process.wait()
