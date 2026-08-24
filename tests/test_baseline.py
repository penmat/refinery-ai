import json
import shutil
import sys
from pathlib import Path

from refinery_ai.discovery import discover
from refinery_ai.models import ModernizationGoal
from refinery_ai.planning import rule_based_plan
from refinery_ai.verification import verify

FIXTURES = Path(__file__).parent / "fixtures"


def _copy_fixture(tmp_path: Path) -> Path:
    repository = tmp_path / "legacy-java"
    shutil.copytree(FIXTURES / "legacy-java", repository)
    return repository


def _tree(repository: Path) -> set[str]:
    return {
        str(path.relative_to(repository)) + ("/" if path.is_dir() else "")
        for path in repository.rglob("*")
    }


def test_golden_discovery_and_plan_are_stable(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path)
    snapshot = discover(repository)
    discovery = snapshot.model_dump(
        mode="json", exclude={"repository", "discovered_at", "git_branch", "dirty"}
    )
    plan = rule_based_plan(
        snapshot, ModernizationGoal(description="Upgrade Java", target_java=17)
    ).model_dump(mode="json")

    assert discovery == json.loads((FIXTURES / "golden/discovery.json").read_text())
    assert plan == json.loads((FIXTURES / "golden/plan.json").read_text())


def test_discovery_smoke_test_records_zero_write_footprint(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path)
    before = _tree(repository)

    discover(repository)

    assert _tree(repository) == before


def test_command_baseline_captures_timing_failure_and_output_limit(tmp_path: Path) -> None:
    success = verify(tmp_path, [sys.executable, "-c", "print('green')"])
    failure = verify(tmp_path, [sys.executable, "-c", "raise SystemExit(9)"])
    large = verify(tmp_path, [sys.executable, "-c", "print('x' * 1000)"], output_limit=32)

    assert success.green and success.stdout == "green\n"
    assert success.duration_seconds >= 0
    assert failure.exit_code == 9 and failure.duration_seconds >= 0
    assert len(large.stdout) == 32
