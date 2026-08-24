from pathlib import Path
from typing import assert_type

from refinery_ai.models import DiscoverySnapshot
from refinery_ai.repository import LegacyRepositoryEnvironment, RepositoryEnvironment


def test_legacy_environment_satisfies_protocol() -> None:
    environment: RepositoryEnvironment = LegacyRepositoryEnvironment()
    assert_type(environment, RepositoryEnvironment)


def test_legacy_environment_preserves_discovery_behavior(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text("plugins { java }")

    snapshot = LegacyRepositoryEnvironment().discover(tmp_path)

    assert isinstance(snapshot, DiscoverySnapshot)
    assert snapshot.build_system.value == "gradle"
