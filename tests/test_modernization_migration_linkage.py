import hashlib
import json
from pathlib import Path

from refinery_ai.migrations.execution_state import MigrationPolicy, RunManifest
from refinery_ai.models import BuildSystem, DiscoverySnapshot, PersistedMigrationPlan


def test_legacy_manifest_digest_unchanged(tmp_path):
    plan = PersistedMigrationPlan(
        plan_id="old",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
    )
    legacy = plan.model_dump(mode="json")
    legacy.pop("modernization_plan_id", None)
    legacy.pop("modernization_plan_digest", None)
    manifest = RunManifest(
        run_id="r",
        repository=tmp_path,
        state_directory=Path(".refinery"),
        plan=plan,
        starting_revision="abc",
        policy=MigrationPolicy(),
        publish=True,
    )
    assert (
        manifest.plan_digest
        == hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    )
    linked = plan.model_copy(
        update={"modernization_plan_id": "new", "modernization_plan_digest": "digest"}
    )
    assert manifest.model_copy(update={"plan": linked}).plan_digest != manifest.plan_digest
