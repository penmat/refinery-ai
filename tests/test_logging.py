from pathlib import Path

from typer.testing import CliRunner

from refinery_ai.cli import app
from refinery_ai.logging import configure_logging, get_logger, safe_command


def test_safe_command_redacts_inline_and_following_secrets() -> None:
    rendered = safe_command(
        ["tool", "--token", "token-value", "--api-key=key-value", "--project", "example"]
    )

    assert "token-value" not in rendered
    assert "key-value" not in rendered
    assert "[REDACTED]" in rendered
    assert "example" in rendered


def test_cli_logs_commands_and_inspectors_to_stderr(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/example/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("package example; class App {}")
    result = CliRunner().invoke(
        app,
        ["analyze", str(tmp_path), "--no-dependencies", "--no-sonarqube", "--json"],
    )

    assert result.exit_code == 0
    assert "Running command" in result.stderr
    assert "Running inspector: discovery" in result.stderr
    assert "Completed inspector: source" in result.stderr
    assert "Running inspector" not in result.stdout


def test_detailed_logging_can_be_routed_to_a_file(tmp_path: Path) -> None:
    log_file = tmp_path / "refinery.log"
    configure_logging(log_file=log_file)

    get_logger("test").info("diagnostic detail")

    assert "diagnostic detail" in log_file.read_text()
