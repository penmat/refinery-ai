from pathlib import Path

from typer.testing import CliRunner

from refinery_ai.cli import app
from refinery_ai.logging import safe_command


def test_safe_command_redacts_inline_and_following_secrets() -> None:
    rendered = safe_command(
        ["tool", "--token", "token-value", "--api-key=key-value", "--project", "example"]
    )

    assert "token-value" not in rendered
    assert "key-value" not in rendered
    assert "[REDACTED]" in rendered
    assert "example" in rendered


def test_cli_logs_commands_and_inspectors_to_stderr(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["assess", str(tmp_path), "--no-dependencies", "--no-sonarqube", "--json"],
    )

    assert result.exit_code == 0
    assert "Running command" in result.stderr
    assert "Running inspector: discovery" in result.stderr
    assert "Completed inspector: source" in result.stderr
    assert "Running inspector" not in result.stdout
