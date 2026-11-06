from pathlib import Path

import pytest

from refinery_ai.migrations.run_store import MigrationRunStore
from refinery_ai.migrations.execution_state import MigrationPolicy, RunManifest
from refinery_ai.models import BuildSystem, DiscoverySnapshot, PersistedMigrationPlan


def manifest(root: Path) -> RunManifest:
    return RunManifest(run_id='a'*32, repository=root.resolve(), state_directory=Path('.refinery'),
        plan=PersistedMigrationPlan(plan_id='plan', snapshot=DiscoverySnapshot(repository=root, build_system=BuildSystem.MAVEN), steps=[]),
        starting_revision='abc', policy=MigrationPolicy(), publish=True)


def test_manifest_roundtrip_is_immutable_and_versioned(tmp_path: Path) -> None:
    store = MigrationRunStore(tmp_path, Path('.refinery'), 'a'*32)
    value = manifest(tmp_path)
    store.create(value)
    assert store.load_manifest() == value
    assert store.load_manifest().schema_version == 1
    assert len(value.plan_digest) == 64
    with pytest.raises(FileExistsError):
        store.create(value)


def test_bad_version_and_path_escape_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        MigrationRunStore(tmp_path, Path('.refinery'), '../escape')
    store = MigrationRunStore(tmp_path, Path('.refinery'), 'a'*32)
    store.create(manifest(tmp_path))
    path = store.root / 'manifest.json'
    path.write_text(path.read_text().replace('"schema_version": 1', '"schema_version": 99'))
    with pytest.raises(ValueError):
        store.load_manifest()
