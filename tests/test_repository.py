from pathlib import Path
from typing import assert_type

from refinery_ai.harness import HarnessRepositoryEnvironment
from refinery_ai.models import DiscoverySnapshot
from refinery_ai.repository import RepositoryEnvironment


def test_harness_environment_satisfies_protocol(tmp_path: Path) -> None:
    environment: RepositoryEnvironment = HarnessRepositoryEnvironment(tmp_path)
    assert_type(environment, RepositoryEnvironment)


def test_harness_environment_discovers_gradle(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text("plugins { java }")

    snapshot = HarnessRepositoryEnvironment(tmp_path).discover(tmp_path)

    assert isinstance(snapshot, DiscoverySnapshot)
    assert snapshot.build_system.value == "gradle"
