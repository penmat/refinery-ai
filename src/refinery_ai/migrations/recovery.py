"""Recipe-boundary recovery reconciles receipts and Git before graph re-entry."""

from __future__ import annotations

import json
import os

from langgraph.checkpoint.sqlite import SqliteSaver

from refinery_ai.migrations.execution_state import (
    MigrationExecutionState,
    MigrationPolicy,
    RecoveryPoint,
)
from refinery_ai.migrations.process_safety import MigrationLease
from refinery_ai.migrations.run_store import MigrationRunStore
from refinery_ai.migrations.worktrees import GitWorktreeManager
from refinery_ai.models import PersistedMigrationPlan, RunStatus


def recover_migration(
    store: MigrationRunStore, policy: MigrationPolicy, *, lease: MigrationLease
) -> RecoveryPoint:
    if os.name != "posix":
        raise ValueError("Safe resume requires POSIX support")
    lease.assert_children_stopped()
    manifest = store.load_manifest()
    if manifest.policy != policy:
        raise ValueError("Migration repair policy changed; refusing resume")
    original = store.repository / store.state_directory / "plans" / f"{manifest.plan.plan_id}.json"
    if not original.resolve().is_relative_to(
        (store.repository / store.state_directory / "plans").resolve()
    ):
        raise ValueError("Pinned plan path is unsafe")
    if (
        not original.exists()
        or PersistedMigrationPlan.model_validate_json(original.read_text()) != manifest.plan
    ):
        raise ValueError("Pinned migration plan changed or is missing")
    manager = GitWorktreeManager(
        store.repository, store.state_directory, store.run_id, manifest.plan.plan_id
    )
    manager._ensure_clean()
    current = manager._git(store.repository, "rev-parse", "HEAD").strip()
    publication = store.load_publication()
    allowed = {manifest.starting_revision}
    if publication is not None:
        if publication.starting_revision != manifest.starting_revision:
            raise ValueError("Publication provenance does not match")
        allowed.add(publication.target_revision)
    if current not in allowed:
        raise ValueError("Source checkout moved; refusing resume")
    generation = store.generation()
    point = RecoveryPoint(revision=manifest.starting_revision, generation=generation + 1)
    point.baseline = store.load_baseline()
    if point.baseline is not None:
        if (
            point.baseline.revision != manifest.starting_revision
            or not point.baseline.verification.green
        ):
            raise ValueError("Invalid baseline receipt")
        point.java = point.baseline.java
    for receipt in store.load_steps():
        if (
            receipt.step != point.next_step
            or receipt.step > len(manifest.plan.steps)
            or receipt.run_id != manifest.run_id
            or receipt.plan_digest != manifest.plan_digest
            or receipt.recipe != manifest.plan.steps[receipt.step - 1]
            or receipt.base_revision != point.revision
            or not receipt.verification.green
            or receipt.result.status is not RunStatus.COMPLETED
            or receipt.result.checkpoint != receipt.checkpoint
            or point.baseline is None
        ):
            raise ValueError("Invalid or noncontiguous verified recipe receipt")
        revision = manager._git(
            store.repository, "rev-parse", f"refs/refinery/{store.run_id}/step-{receipt.step:03d}"
        ).strip()
        if revision != receipt.checkpoint:
            raise ValueError("Verified recipe ref does not match receipt")
        if revision != point.revision:
            parent = manager._git(store.repository, "rev-parse", f"{revision}^").strip()
            if parent != point.revision:
                raise ValueError("Verified recipe predecessor does not match")
        point.results.append(receipt.result)
        point.revision = revision
        point.java = receipt.java
        point.next_step += 1
    if not store.checkpoints.exists():
        raise ValueError("Migration checkpoint store is missing")
    if store.checkpoints.exists():
        with SqliteSaver.from_conn_string(str(store.checkpoints)) as saver:
            checkpoint = saver.get_tuple(
                {"configurable": {"thread_id": f"{store.run_id}:{generation}"}}
            )
        if checkpoint is not None:
            saved = checkpoint.checkpoint.get("channel_values", {}).get("data")
            if saved is not None:
                saved = MigrationExecutionState.model_validate(saved)
                if saved.status == "failed":
                    raise ValueError("Terminal migration failure cannot be resumed")
                if len(
                    [result for result in saved.results if result.status is RunStatus.COMPLETED]
                ) > len(point.results):
                    raise ValueError("A previously verified recipe receipt is missing")
    stored = store.load_result()
    runtime = json.loads(store.path("run.json").read_text())
    if (stored is not None and stored.status is RunStatus.FAILED) or runtime.get(
        "status"
    ) == "failed":
        raise ValueError("Terminal migration failure cannot be resumed")
    if publication is not None and publication.target_revision != point.revision:
        raise ValueError("Publication target is not the verified prefix")
    if stored is not None:
        if (
            stored.final_revision != point.revision
            or len(stored.steps) != len(point.results)
            or (stored.published_revision is not None and current != stored.published_revision)
        ):
            raise ValueError("Stored migration result does not match verified state")
        point.result = stored
        return point
    for metadata in store.root.glob("step-*/step.json"):
        if json.loads(metadata.read_text()).get("status") == "failed":
            raise ValueError("Terminal recipe failure cannot be resumed")
    # All validation above is read-only. Only now may abandoned owned state be retired.
    archive = store.path(f"interrupted/generation-{generation}")
    if manager.worktree_root.exists():
        for workspace in manager.worktree_root.iterdir():
            if workspace.is_symlink():
                raise ValueError("Owned worktree must not be a symlink")
            name = workspace.name
            if name.startswith("step-"):
                index = int(name.split("-")[1])
                if index < point.next_step:
                    continue
            elif name.startswith("baseline"):
                if point.baseline is not None:
                    continue
            else:
                continue
            manager._git(store.repository, "worktree", "remove", "--force", str(workspace))
    for artifacts in list(store.root.glob("step-*")):
        index = int(artifacts.name.split("-")[1])
        if index < point.next_step:
            continue
        metadata = artifacts / "step.json"
        if metadata.exists() and json.loads(metadata.read_text()).get("status") == "failed":
            raise ValueError("Terminal recipe failure cannot be resumed")
        store.path(str(artifacts.relative_to(store.root)))
        archive.mkdir(parents=True, exist_ok=True)
        artifacts.rename(archive / artifacts.name)
    if point.baseline is None and store.path("baseline").exists():
        archive.mkdir(parents=True, exist_ok=True)
        store.path("baseline").rename(archive / "baseline")
    store.save_generation(point.generation)
    return point
