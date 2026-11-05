"""One-shot pi integration with an installed fake executable, without model calls."""
import json
import sys
import time
from pathlib import Path

import pytest

from refinery_ai.agents.pi_repair import PiRepairAgent
from refinery_ai.config import Settings
from refinery_ai.models import RecipeRecommendation, VerificationResult
from test_repair_snapshot import git, repository


STEP = RecipeRecommendation(recipe='example.Migrate', reason='upgrade', evidence=['build'], expected_benefit='build passes', priority='high', confidence=1)
FAILURE = VerificationResult(command=['mvn', 'test'], exit_code=1, stderr='cannot find symbol', duration_seconds=0)


def executable(tmp_path: Path, body: str) -> Path:
    script = tmp_path / 'fake-pi'
    script.write_text(f'#!{sys.executable}\n' + body)
    script.chmod(0o755)
    return script


def agent(script: Path, **kwargs) -> PiRepairAgent:
    return PiRepairAgent(Settings(_env_file=None, pi_executable=str(script), **kwargs))


def test_pi_uses_stdin_worktree_and_only_explicit_overrides(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    root.mkdir()
    repository(root)
    script = executable(tmp_path, '''import sys, json, os
from pathlib import Path
Path('source').write_text(json.dumps({'args': sys.argv[1:], 'prompt': sys.stdin.read(), 'cwd': os.getcwd()}))
''')
    repairer = agent(script, coding_agent_provider='custom', coding_agent_model='org/model:high')
    result = repairer.repair(root, STEP, FAILURE)
    assert result.green
    payload = json.loads((root / 'source').read_text())
    assert payload['cwd'] == str(root)
    assert payload['args'] == ['--print', '--no-session', '--provider', 'custom', '--model', 'org/model:high']
    assert 'example.Migrate' in payload['prompt']
    assert 'cannot find symbol' in payload['prompt']
    repairer.accept_last()
    assert (root / 'source').read_text().startswith('{')
    repairer = agent(script)
    assert repairer.repair(root, STEP, FAILURE).green
    assert json.loads((root / 'source').read_text())['args'] == ['--print', '--no-session']
    repairer.rollback_last()
    assert json.loads((root / 'source').read_text()) == payload


@pytest.mark.parametrize('body,diagnostic', [
    ("from pathlib import Path\nPath('source').write_text('bad')\nraise SystemExit(2)\n", 'exit code 2'),
    ('print("nothing")\n', 'did not change'),
    ("import subprocess\nsubprocess.run(['git','add','-A'],check=True)\n", 'Git'),
    ("from pathlib import Path\nPath('source').write_text('committed')\nimport subprocess\nsubprocess.run(['git','add','-A'],check=True)\nsubprocess.run(['git','commit','-qm','bad'],check=True)\n", 'Git'),
])
def test_failed_attempt_restores_prior_state(tmp_path: Path, body: str, diagnostic: str) -> None:
    root = repository(tmp_path)
    (root / 'source').write_text('earlier accepted repair')
    before = git(root, 'rev-parse', 'HEAD')
    script_dir = tmp_path / 'target'
    script_dir.mkdir()
    repairer = agent(executable(script_dir, body))
    result = repairer.repair(root, STEP, FAILURE)
    assert not result.green
    assert diagnostic in result.stderr
    assert (root / 'source').read_text() == 'earlier accepted repair'
    assert git(root, 'rev-parse', 'HEAD') == before


def test_missing_executable_returns_diagnostic(tmp_path: Path) -> None:
    repository(tmp_path)
    result = agent(tmp_path / 'missing').repair(tmp_path, STEP, FAILURE)
    assert not result.green
    assert 'installed' in result.stderr


def test_output_is_bounded_and_source_edits_can_be_reverted(tmp_path: Path) -> None:
    root = repository(tmp_path)
    target = root / 'target'
    target.mkdir()
    repairer = agent(executable(target, "from pathlib import Path\nimport sys\nPath('source').write_text('fixed')\nprint('x'*100000)\nprint('y'*100000,file=sys.stderr)\n"), max_output_characters=100)
    result = repairer.repair(root, STEP, FAILURE)
    assert result.green
    assert len(result.stdout) <= 100
    assert len(result.stderr) <= 100
    repairer.rollback_last()
    assert (root / 'source').read_bytes() == b'original\x00'


def test_timeout_kills_descendants_before_rollback(tmp_path: Path) -> None:
    root = repository(tmp_path)
    target = root / 'target'
    target.mkdir()
    child = "import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(2); Path('late').write_text('bad')"
    script = executable(target, f"import subprocess,sys,time\nfrom pathlib import Path\nPath('source').write_text('partial')\nsubprocess.Popen([sys.executable,'-c',{child!r}])\ntime.sleep(10)\n")
    repairer = agent(script, command_timeout_seconds=1)
    result = repairer.repair(root, STEP, FAILURE)
    assert not result.green
    assert 'timed out' in result.stderr
    time.sleep(1.5)
    assert not (root / 'late').exists()
    assert (root / 'source').read_bytes() == b'original\x00'


@pytest.mark.parametrize('option', ['coding_agent_provider', 'coding_agent_model', 'pi_executable'])
def test_blank_configuration_is_rejected(option: str) -> None:
    with pytest.raises(ValueError, match='blank'):
        Settings(_env_file=None, **{option: '  '})


def test_cancellation_stops_process_and_restores_partial_edits(tmp_path: Path, monkeypatch) -> None:
    import refinery_ai.agents.pi_repair as adapter

    root = repository(tmp_path)
    target = root / 'target'
    target.mkdir()
    script = executable(target, "import time\nfrom pathlib import Path\nPath('source').write_text('partial')\ntime.sleep(10)\n")
    real_clock = time.monotonic
    cancelled = False

    def clock() -> float:
        nonlocal cancelled
        if not cancelled and (root / 'source').read_bytes() == b'partial':
            cancelled = True
            raise KeyboardInterrupt
        return real_clock()

    monkeypatch.setattr(adapter.time, 'monotonic', clock)
    repairer = agent(script)
    with pytest.raises(KeyboardInterrupt):
        repairer.repair(root, STEP, FAILURE)
    assert cancelled
    assert (root / 'source').read_bytes() == b'original\x00'
