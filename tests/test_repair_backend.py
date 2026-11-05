"""Selectable repair backend and CLI configuration behavior."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from refinery_ai.agents.pi_repair import PiRepairAgent
from refinery_ai.agents.repair_backend import build_repair_stages
from refinery_ai.cli import app
from refinery_ai.config import Settings
from refinery_ai.models import MigrationRun, RepairBackend, RepairMode, RunStatus


def test_default_and_disabled_backends_keep_deterministic_repair_only() -> None:
    assert Settings(_env_file=None).repair_backend is RepairBackend.INTERNAL
    for backend in RepairBackend:
        stages = build_repair_stages(Settings(_env_file=None, repair_backend=backend))
        assert len(stages) == 1
        assert stages[0].name == "deterministic"
        assert not stages[0].uses_model


def test_pi_selection_does_not_construct_an_internal_model() -> None:
    stages = build_repair_stages(
        Settings(_env_file=None, repair_backend="pi", repair_mode="configured-provider")
    )
    assert isinstance(stages[1].repairer, PiRepairAgent)
    assert stages[1].name == "pi"
    assert stages[1].uses_model
    assert stages[1].repairer.settings.coding_agent_model is None


def test_local_pi_policy_is_explicitly_rejected() -> None:
    with pytest.raises(ValueError, match="local"):
        build_repair_stages(Settings(_env_file=None, repair_backend="pi", repair_mode="local"))


def test_internal_local_endpoint_validation_is_preserved() -> None:
    with pytest.raises(ValueError, match="loopback"):
        build_repair_stages(
            Settings(
                _env_file=None, repair_mode="local", ollama_base_url="https://remote.example/v1"
            )
        )


def test_internal_enabled_selection_uses_existing_agent(monkeypatch) -> None:
    from test_ai_repair import install_repair_graph

    from refinery_ai.agents.ai_repair import AiRepairAgent

    install_repair_graph(monkeypatch, {})
    stages = build_repair_stages(
        Settings(
            _env_file=None,
            repair_mode=RepairMode.CONFIGURED_PROVIDER,
            OPENAI_API_KEY="test-only-placeholder",
        )
    )
    assert isinstance(stages[1].repairer, AiRepairAgent)
    assert stages[1].name == "AI"


@pytest.mark.parametrize("command", ["migrate", "run"])
def test_cli_explicit_agent_options_override_environment(
    tmp_path: Path, monkeypatch, command: str
) -> None:
    import refinery_ai.cli as cli

    monkeypatch.setenv("REFINERY_REPAIR_BACKEND", "internal")
    monkeypatch.setenv("REFINERY_CODING_AGENT_MODEL", "old")
    captured = []

    class Runner:
        def __init__(self, *args, **kwargs):
            captured.extend(kwargs["repair_stages"])

        def run(self, *args, **kwargs):
            return MigrationRun(plan_id="test", status=RunStatus.COMPLETED)

    class ProfileRunner:
        def __init__(self, *args, **kwargs):
            self.migrate = kwargs["migrate"]

        def run(self):
            self.migrate("test", None)
            raise RuntimeError("profile-options-captured")

    monkeypatch.setattr(cli, "MigrationRunner", Runner)
    monkeypatch.setattr(cli, "ProfileRunner", ProfileRunner)
    args = [command]
    if command == "migrate":
        args += [str(tmp_path)]
    else:
        args += ["--repository", str(tmp_path), "--profile", "spring-boot-3.2-java-17"]
    args += [
        "--apply",
        "--repair-mode",
        "configured-provider",
        "--repair-backend",
        "pi",
        "--coding-agent-model",
        "custom/model",
        "--coding-agent-provider",
        "custom",
    ]
    result = CliRunner().invoke(app, args)
    assert captured, result.output or repr(result.exception)
    assert isinstance(captured[1].repairer, PiRepairAgent)
    settings = captured[1].repairer.settings
    assert settings.coding_agent_model == "custom/model"
    assert settings.coding_agent_provider == "custom"


@pytest.mark.parametrize("command", ["migrate", "run"])
def test_cli_rejects_pi_local_before_repository_side_effects(tmp_path: Path, command: str) -> None:
    args = (
        [command, str(tmp_path)]
        if command == "migrate"
        else [command, "--repository", str(tmp_path)]
    )
    result = CliRunner().invoke(app, [*args, "--repair-mode", "local", "--repair-backend", "pi"])
    assert result.exit_code == 2
    assert "local" in result.output
    assert not (tmp_path / ".refinery").exists()


def test_cli_rejects_blank_model_override(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["migrate", str(tmp_path), "--coding-agent-model", " "])
    assert result.exit_code == 2
    assert "blank" in result.output
    assert not (tmp_path / ".refinery").exists()
