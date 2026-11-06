"""Coding-agent settings across CLI and persisted recovery policy."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from refinery_ai.agents.repair_backend import migration_policy
from refinery_ai.cli import app
from refinery_ai.config import Settings
from refinery_ai.migrations.execution_state import MigrationPolicy, RunManifest
from refinery_ai.migrations.run_store import MigrationRunStore
from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    MigrationRun,
    PersistedMigrationPlan,
    RunStatus,
)


@pytest.mark.parametrize("command", ["migrate", "run"])
@pytest.mark.parametrize("backend", ["opencode", "codex", "claude"])
def test_cli_forwards_agent_permissions_and_executable(tmp_path, monkeypatch, command, backend):
    import refinery_ai.cli as cli

    captured = []

    class Runner:
        def __init__(self, *args, **kwargs):
            captured.append(kwargs)

        def run(self, *args, **kwargs):
            return MigrationRun(plan_id="test", status=RunStatus.COMPLETED)

    class ProfileRunner:
        def __init__(self, *args, **kwargs):
            self.migrate = kwargs["migrate"]

        def run(self):
            self.migrate("test", None)
            raise RuntimeError("captured")

    monkeypatch.setattr(cli, "MigrationRunner", Runner)
    monkeypatch.setattr(cli, "ProfileRunner", ProfileRunner)
    monkeypatch.setenv("REFINERY_CODING_AGENT_PERMISSIONS", "managed")
    args = (
        ["migrate", str(tmp_path)]
        if command == "migrate"
        else ["run", "--repository", str(tmp_path), "--profile", "spring-boot-3.2-java-17"]
    )
    result = CliRunner().invoke(
        app,
        [
            *args,
            "--apply",
            "--repair-mode",
            "configured-provider",
            "--repair-backend",
            backend,
            "--coding-agent-permissions",
            "inherit",
            f"--{backend}-executable",
            "/custom/agent",
            "--coding-agent-model",
            "custom-model",
        ],
    )
    assert captured, result.output or repr(result.exception)
    settings = captured[0]["repair_stages"][1].repairer.settings
    assert settings.coding_agent_permissions == "inherit"
    assert getattr(settings, f"{backend}_executable") == "/custom/agent"
    assert settings.coding_agent_model == "custom-model"
    assert captured[0]["policy"].coding_agent_permissions == "inherit"


@pytest.mark.parametrize("source", ["omitted", "flag", "environment"])
def test_old_manifest_inherits_historical_permissions(tmp_path, monkeypatch, source):
    import refinery_ai.cli as cli

    monkeypatch.chdir(tmp_path)
    run_id = "c" * 32
    store = MigrationRunStore(tmp_path, Path(".refinery"), run_id)
    plan = PersistedMigrationPlan(
        plan_id="old",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[],
    )
    manifest = RunManifest(
        run_id=run_id,
        repository=tmp_path,
        state_directory=Path(".refinery"),
        plan=plan,
        starting_revision="abc",
        policy=MigrationPolicy(),
        publish=True,
    )
    store.create(manifest)
    path = store.path("manifest.json")
    payload = json.loads(path.read_text())
    for field in (
        "coding_agent_permissions",
        "opencode_executable",
        "codex_executable",
        "claude_executable",
    ):
        payload["policy"].pop(field, None)
    path.write_text(json.dumps(payload))
    captured = []

    class Runner:
        def __init__(self, *args, **kwargs):
            captured.append(kwargs["policy"])

        def run(self, *args, **kwargs):
            return MigrationRun(plan_id="old", status=RunStatus.COMPLETED, run_id=run_id)

    monkeypatch.setattr(cli, "MigrationRunner", Runner)
    args = ["migrate", str(tmp_path), "--resume-run", run_id]
    if source == "flag":
        args += ["--coding-agent-permissions", "managed"]
    elif source == "environment":
        monkeypatch.setenv("REFINERY_CODING_AGENT_PERMISSIONS", "managed")
    result = CliRunner().invoke(app, args)
    if source == "omitted":
        assert result.exit_code == 0, result.output or repr(result.exception)
        assert captured[0].coding_agent_permissions == "inherit"
    else:
        assert result.exit_code == 2
        assert not captured
        assert "conflicts" in result.output


def test_new_policy_records_managed_and_executable_paths():
    policy = migration_policy(Settings(_env_file=None, codex_executable="/custom/codex"))
    assert policy.coding_agent_permissions == "managed"
    assert policy.codex_executable == "/custom/codex"
    assert MigrationPolicy.model_validate({}).coding_agent_permissions == "inherit"


def test_corpus_cli_passes_agent_configuration(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from test_corpus_run import _corpus

    import refinery_ai.cli as cli

    corpus = _corpus(tmp_path / "corpus.yaml")
    captured = []

    class Runner:
        def __init__(self, *args, **kwargs):
            captured.append(kwargs)

        def run(self, selected):
            return SimpleNamespace(projects=[])

    monkeypatch.setattr(cli, "CorpusRunner", Runner)
    result = CliRunner().invoke(
        app,
        [
            "run-corpus",
            str(corpus),
            "--repair-backend",
            "codex",
            "--coding-agent-permissions",
            "inherit",
            "--codex-executable",
            "/custom/codex",
        ],
    )
    assert captured, result.output or repr(result.exception)
    assert captured[0]["coding_agent_permissions"] == "inherit"
    assert captured[0]["codex_executable"] == "/custom/codex"
    assert captured[0]["repair_backend"] == "codex"


@pytest.mark.parametrize("backend", ["opencode", "codex", "claude"])
@pytest.mark.parametrize("command", ["migrate", "run"])
def test_external_local_rejected_before_artifacts(tmp_path, command, backend):
    args = (
        [command, str(tmp_path)]
        if command == "migrate"
        else [command, "--repository", str(tmp_path)]
    )
    result = CliRunner().invoke(app, [*args, "--repair-backend", backend, "--repair-mode", "local"])
    assert result.exit_code == 2
    assert "local" in result.output
    assert not (tmp_path / ".refinery").exists()
