"""Atomic migration provenance and verified-recipe receipts."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from pydantic import BaseModel

from refinery_ai.migrations.execution_state import (
    BaselineReceipt,
    PublicationIntent,
    RunManifest,
    StepReceipt,
)
from refinery_ai.models import MigrationRun


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as output:
        json.dump(data, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    if os.name == "posix":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def safe_state_root(repository: Path, state_directory: Path) -> Path:
    candidate = repository.resolve() / state_directory
    if not state_directory.is_absolute():
        current = repository.resolve()
        for part in state_directory.parts:
            if part == "..":
                raise ValueError("State directory must not escape the repository")
            current = current / part
            if current.is_symlink():
                raise ValueError("State directories must not be symlinks")
    elif candidate.is_symlink():
        raise ValueError("State directory must not be a symlink")
    return candidate.resolve()


def safe_owned_path(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Migration artifact path escapes state directory")
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Migration artifacts must not be symlinks")
    return path


class MigrationRunStore:
    def __init__(self, repository: Path, state_directory: Path, run_id: str) -> None:
        if re.fullmatch(r"[a-f0-9]{32}", run_id) is None:
            raise ValueError("Invalid migration run ID")
        self.repository = repository.resolve()
        self.state_directory = state_directory
        state = safe_state_root(self.repository, state_directory)
        self.state_root = state
        self.root = safe_owned_path(state, f"migration-runs/{run_id}")
        self.run_id = run_id
        self.checkpoints = safe_owned_path(state, "migration-checkpoints.sqlite")
        self.journal = safe_owned_path(state, f"migration-events/{run_id}.jsonl")
        self.path("manifest.json")

    def path(self, name: str) -> Path:
        return safe_owned_path(self.state_root, f"migration-runs/{self.run_id}/{name}")

    def create(self, manifest: RunManifest) -> None:
        path = self.path("manifest.json")
        if path.exists():
            raise FileExistsError("Migration manifest already exists")
        atomic_json(path, manifest)

    def load_manifest(self) -> RunManifest:
        payload = json.loads(self.path("manifest.json").read_text())
        manifest = RunManifest.model_validate(payload)
        if (
            manifest.run_id != self.run_id
            or manifest.repository.resolve() != self.repository
            or manifest.state_directory != self.state_directory
            or payload.get("plan_digest") != manifest.plan_digest
        ):
            raise ValueError("Migration manifest provenance does not match")
        return manifest

    def save_baseline(self, receipt: BaselineReceipt) -> None:
        atomic_json(self.path("baseline-receipt.json"), receipt)

    def load_baseline(self) -> BaselineReceipt | None:
        path = self.path("baseline-receipt.json")
        return BaselineReceipt.model_validate_json(path.read_text()) if path.exists() else None

    def save_step(self, receipt: StepReceipt) -> None:
        atomic_json(self.path(f"step-{receipt.step:03d}/step.json"), receipt)

    def load_steps(self) -> list[StepReceipt]:
        receipts = []
        for path in sorted(self.root.glob("step-*/step.json")):
            self.path(str(path.relative_to(self.root)))
            payload = json.loads(path.read_text())
            if payload.get("schema_version") is None:
                # Legacy-shaped metadata in a new-format run is an incomplete commit window.
                continue
            receipts.append(StepReceipt.model_validate(payload))
        return receipts

    def save_publication(self, intent: PublicationIntent) -> None:
        atomic_json(self.path("publication.json"), intent)

    def load_publication(self) -> PublicationIntent | None:
        path = self.path("publication.json")
        return PublicationIntent.model_validate_json(path.read_text()) if path.exists() else None

    def save_result(self, result: MigrationRun) -> None:
        atomic_json(self.path("result.json"), result)

    def load_result(self) -> MigrationRun | None:
        path = self.path("result.json")
        return MigrationRun.model_validate_json(path.read_text()) if path.exists() else None

    def generation(self) -> int:
        path = self.path("generation.json")
        return int(json.loads(path.read_text())["generation"]) if path.exists() else 0

    def save_generation(self, generation: int) -> None:
        atomic_json(self.path("generation.json"), {"generation": generation})

    @classmethod
    def find_bound_run(cls, repository: Path, state_directory: Path, plan_id: str) -> str | None:
        found = []
        for path in (repository / state_directory / "migration-runs").glob("*/manifest.json"):
            store = cls(repository, state_directory, path.parent.name)
            if store.load_manifest().plan.plan_id == plan_id:
                found.append(path.parent.name)
        if len(found) > 1:
            raise ValueError("Multiple migrations are bound to this plan; specify --resume-run")
        return found[0] if found else None
