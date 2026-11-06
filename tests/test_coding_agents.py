"""Executable-stub contracts for external agents; no model/provider calls."""

import json

import pytest
from test_pi_repair import FAILURE, STEP, executable
from test_repair_snapshot import repository

from refinery_ai.agents.repair_backend import build_repair_stages
from refinery_ai.config import Settings


def make_agent(backend, script, **kwargs):
    settings = Settings(
        _env_file=None,
        repair_backend=backend,
        repair_mode="configured-provider",
        **{f"{backend}_executable": str(script)},
        **kwargs,
    )
    return build_repair_stages(settings)[1].repairer


@pytest.mark.parametrize("backend", ["opencode", "codex", "claude"])
@pytest.mark.parametrize("permissions", ["managed", "inherit"])
def test_agent_command_stdin_worktree_and_transaction(tmp_path, backend, permissions):
    root = repository(tmp_path)
    script = executable(
        tmp_path,
        """import sys,json,os
from pathlib import Path
payload = {'args':sys.argv[1:], 'prompt':sys.stdin.read(), 'cwd':os.getcwd()}
Path('source').write_text(json.dumps(payload))
""",
    )
    agent = make_agent(
        backend, script, coding_agent_permissions=permissions, coding_agent_model="test-model"
    )
    result = agent.repair(root, STEP, FAILURE)
    assert result.green, result.stderr
    payload = json.loads((root / "source").read_text())
    args = payload["args"]
    assert payload["cwd"] == str(root)
    assert "example.Migrate" in payload["prompt"] and "cannot find symbol" in payload["prompt"]
    assert "cannot find symbol" not in " ".join(args)
    assert args[args.index("--model") + 1] == "test-model"
    base = {
        "opencode": ["run"],
        "codex": ["exec", "--ephemeral", "-"],
        "claude": ["--print", "--no-session-persistence"],
    }[backend]
    assert all(value in args for value in base)
    managed = {
        "opencode": ["--auto"],
        "codex": ["--ask-for-approval", "never", "--sandbox", "workspace-write"],
        "claude": [
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            "Read,Edit,Write,Bash,Glob,Grep",
        ],
    }[backend]
    assert (
        all(value in args for value in managed)
        if permissions == "managed"
        else not any(value in args for value in managed)
    )
    agent.rollback_last()
    assert (root / "source").read_bytes() == b"original\x00"
    assert agent.repair(root, STEP, FAILURE).green
    agent.accept_last()
    assert (root / "source").read_bytes() != b"original\x00"


@pytest.mark.parametrize("backend", ["opencode", "codex", "claude"])
@pytest.mark.parametrize(
    "body,diagnostic",
    [
        (
            "from pathlib import Path\nPath('source').write_text('bad')\nraise SystemExit(2)",
            "exit code 2",
        ),
        ("print('nothing')", "did not change"),
        ("import subprocess\nsubprocess.run(['git','add','-A'],check=True)", "Git"),
        (
            "from pathlib import Path\nimport subprocess\n"
            "Path('source').write_text('bad')\n"
            "subprocess.run(['git','add','-A'],check=True)\n"
            "subprocess.run(['git','commit','-qm','bad'],check=True)",
            "Git",
        ),
    ],
)
def test_rejected_candidate_restores_sources(tmp_path, backend, body, diagnostic):
    root = repository(tmp_path)
    agent = make_agent(backend, executable(tmp_path, body))
    result = agent.repair(root, STEP, FAILURE)
    assert not result.green
    assert diagnostic in result.stderr
    assert (root / "source").read_bytes() == b"original\x00"


@pytest.mark.parametrize("backend", ["opencode", "codex", "claude"])
def test_missing_executable_and_timeout_restore_sources(tmp_path, backend):
    root = repository(tmp_path)
    agent = make_agent(backend, tmp_path / "absent")
    result = agent.repair(root, STEP, FAILURE)
    assert not result.green and "installed and configured" in result.stderr
    script = executable(
        tmp_path,
        "from pathlib import Path\nimport time\n"
        "Path('source').write_text('partial')\ntime.sleep(30)",
    )
    agent = make_agent(backend, script, command_timeout_seconds=1)
    result = agent.repair(root, STEP, FAILURE)
    assert not result.green and "timed out" in result.stderr
    assert (root / "source").read_bytes() == b"original\x00"


@pytest.mark.parametrize("backend", ["opencode", "codex", "claude"])
def test_external_local_and_unsupported_provider_rejected(backend):
    with pytest.raises(ValueError, match="local"):
        build_repair_stages(Settings(_env_file=None, repair_backend=backend, repair_mode="local"))
    if backend != "opencode":
        with pytest.raises(ValueError, match="provider"):
            build_repair_stages(
                Settings(
                    _env_file=None,
                    repair_backend=backend,
                    repair_mode="configured-provider",
                    coding_agent_provider="custom",
                )
            )


@pytest.mark.parametrize(
    "provider,model,expected",
    [
        ("custom", "test-model", "custom/test-model"),
        ("custom", "custom/test-model", "custom/test-model"),
        (None, "custom/test-model", "custom/test-model"),
    ],
)
def test_opencode_provider_model_mapping(tmp_path, provider, model, expected):
    agent = make_agent(
        "opencode", tmp_path / "unused", coding_agent_provider=provider, coding_agent_model=model
    )
    command = agent.command()
    assert command[command.index("--model") + 1] == expected


@pytest.mark.parametrize("model", [None, "other/model"])
def test_opencode_inconsistent_provider_rejected(model):
    with pytest.raises(ValueError, match="provider"):
        build_repair_stages(
            Settings(
                _env_file=None,
                repair_backend="opencode",
                repair_mode="configured-provider",
                coding_agent_provider="custom",
                coding_agent_model=model,
            )
        )


def test_permission_setting_defaults_environment_and_validation(monkeypatch):
    assert Settings(_env_file=None).coding_agent_permissions == "managed"
    monkeypatch.setenv("REFINERY_CODING_AGENT_PERMISSIONS", "inherit")
    assert Settings(_env_file=None).coding_agent_permissions == "inherit"
    with pytest.raises(ValueError):
        Settings(_env_file=None, coding_agent_permissions="unknown")
    for backend in ("opencode", "codex", "claude"):
        with pytest.raises(ValueError, match="blank"):
            Settings(_env_file=None, **{f"{backend}_executable": " "})


@pytest.mark.parametrize("backend", ["opencode", "codex", "claude"])
def test_output_limit_and_cancellation(tmp_path, monkeypatch, backend):
    import time

    import refinery_ai.agents.cli_repair as adapter

    root = repository(tmp_path)
    script = executable(
        tmp_path,
        "from pathlib import Path\nimport sys\nPath('source').write_text('fixed')\n"
        "print('x'*100000)\nprint('y'*100000,file=sys.stderr)",
    )
    agent = make_agent(backend, script, max_output_characters=100)
    result = agent.repair(root, STEP, FAILURE)
    assert result.green
    assert len(result.stdout) <= 100 and len(result.stderr) <= 100
    agent.rollback_last()
    script = executable(
        tmp_path,
        "from pathlib import Path\nimport time\n"
        "Path('source').write_text('partial')\ntime.sleep(30)",
    )
    agent = make_agent(backend, script)
    real_clock = time.monotonic

    def clock():
        if (root / "source").read_bytes() == b"partial":
            raise KeyboardInterrupt
        return real_clock()

    monkeypatch.setattr(adapter.time, "monotonic", clock)
    with pytest.raises(KeyboardInterrupt):
        agent.repair(root, STEP, FAILURE)
    assert (root / "source").read_bytes() == b"original\x00"


@pytest.mark.parametrize("permissions", ["managed", "inherit"])
def test_pi_permission_setting_keeps_existing_arguments(tmp_path, permissions):
    from refinery_ai.agents.pi_repair import PiRepairAgent

    agent = PiRepairAgent(Settings(_env_file=None, coding_agent_permissions=permissions))
    assert agent.command() == ["pi", "--print", "--no-session"]


@pytest.mark.parametrize("backend", ["opencode", "codex", "claude"])
def test_timeout_stops_descendants_before_rollback(tmp_path, backend):
    import time

    root = repository(tmp_path)
    child = (
        "import signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "time.sleep(2); Path('late').write_text('bad')"
    )
    script = executable(
        tmp_path,
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "Path('source').write_text('partial')\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r}])\n"
        "time.sleep(30)\n",
    )
    agent = make_agent(backend, script, command_timeout_seconds=1)
    assert not agent.repair(root, STEP, FAILURE).green
    time.sleep(1.5)
    assert not (root / "late").exists()
    assert (root / "source").read_bytes() == b"original\x00"
