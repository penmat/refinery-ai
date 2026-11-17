"""Recoverable destination execution using the existing verified migration engine."""

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import Field

from refinery_ai.config import Settings
from refinery_ai.migrations.execution_state import StepReceipt
from refinery_ai.migrations.run_store import (
    MigrationRunStore,
    atomic_json,
    safe_owned_path,
    safe_state_root,
)
from refinery_ai.models import MigrationRun
from refinery_ai.modernization.evidence import ProjectEvidence, collect_evidence
from refinery_ai.modernization.models import Contract, ModernizationIntentPlan
from refinery_ai.modernization.policy import mode_policy
from refinery_ai.modernization.progress import ModernizationProgress
from refinery_ai.modernization.store import IntentPlanStore, intent_digest


class ModernizationRunResult(Contract):
    plan_id: str
    status: str
    cycles: int
    message: str
    limitations: list[str] = Field(default_factory=list)
    objectives: list[dict[str, str]] = Field(default_factory=list)


class ModernizationRunStore:
    def __init__(self, root: Path, state_directory: Path, plan_id: str) -> None:
        if not plan_id.isascii() or not plan_id.isalnum() or len(plan_id) > 128:
            raise ValueError("Invalid modernization plan ID")
        self.state_root = safe_state_root(root, state_directory)
        self.relative = f"modernization-runs/{plan_id}.json"
        self.path = safe_owned_path(self.state_root, self.relative)

    def load(self) -> ModernizationProgress | None:
        safe_owned_path(self.state_root, self.relative)
        return (
            ModernizationProgress.model_validate_json(self.path.read_text())
            if self.path.exists()
            else None
        )

    def save(self, state: ModernizationProgress) -> None:
        safe_owned_path(self.state_root, self.relative)
        safe_owned_path(self.state_root, self.relative + ".tmp")
        atomic_json(self.path, state)


class ModernizationRunner:
    def __init__(
        self,
        repository: Path,
        settings: Settings,
        *,
        collect: Callable[[], ProjectEvidence] | None = None,
        verify_final: Callable[[], bool] | None = None,
        migrate: Callable[[str], MigrationRun] | None = None,
    ) -> None:
        self.repository = repository.resolve()
        self.settings = settings
        self.collect_override, self.verify_override, self.migrate_override = (
            collect,
            verify_final,
            migrate,
        )

    def collect(self, intent: ModernizationIntentPlan) -> ProjectEvidence:
        return (
            self.collect_override()
            if self.collect_override
            else collect_evidence(self.repository, mode_policy(intent.mode), self.settings)
        )

    def verify_final(self, intent: ModernizationIntentPlan) -> bool:
        if self.verify_override:
            return self.verify_override()
        from refinery_ai.build_tools import build_directory
        from refinery_ai.discovery import discover
        from refinery_ai.jdks import JdkResolver
        from refinery_ai.verification import verification_command, verify

        snapshot = discover(self.repository)
        command = verification_command(snapshot)
        if command is None:
            return False
        return verify(
            self.repository / build_directory(snapshot.build_files),
            command,
            timeout=self.settings.command_timeout_seconds,
            environment=JdkResolver().environment(intent.requested_jdk),
        ).green

    def migrate(self, plan_id: str) -> MigrationRun:
        if self.migrate_override:
            return self.migrate_override(plan_id)
        from refinery_ai.agents.repair_backend import build_repair_stages, migration_policy
        from refinery_ai.jdks import JdkResolver
        from refinery_ai.migrations.runner import MigrationRunner
        from refinery_ai.planning_artifacts import MigrationPlanStore

        return MigrationRunner(
            MigrationPlanStore(self.repository, self.settings.state_directory),
            timeout=self.settings.command_timeout_seconds,
            output_limit=self.settings.max_output_characters,
            jdk_resolver=JdkResolver(),
            repair_stages=build_repair_stages(self.settings),
            policy=migration_policy(self.settings),
        ).run(plan_id, apply=True, publish=True, resume_bound_run=True)

    def run(self, plan_id: str, *, apply: bool, max_cycles: int = 20) -> ModernizationRunResult:
        if not apply:
            raise ValueError("Mode runs change sources; pass --apply")
        if max_cycles < 1:
            raise ValueError("max_cycles must be positive")
        intent = IntentPlanStore(self.repository, self.settings.state_directory).load(plan_id)
        if intent.repository.resolve() != self.repository:
            raise ValueError("Saved plan belongs to another repository; regenerate plan")
        if intent.blocked_reasons:
            return ModernizationRunResult(
                plan_id=plan_id,
                status="needs_review",
                cycles=0,
                message="Destination blocked: " + "; ".join(intent.blocked_reasons),
                limitations=intent.limitations,
            )
        # Pin execution settings without storing credentials or environment secrets.
        from refinery_ai.agents.repair_backend import migration_policy

        settings_digest = hashlib.sha256(
            json.dumps(
                {
                    "policy": migration_policy(self.settings).model_dump(mode="json"),
                    "state_directory": str(self.settings.state_directory),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        store = ModernizationRunStore(self.repository, self.settings.state_directory, plan_id)
        progress = store.load() or ModernizationProgress(
            plan_id=plan_id,
            plan_digest=intent_digest(intent),
            expected_revision=intent.source_revision,
            build_fingerprint=intent.build_fingerprint,
            source_fingerprint=intent.source_fingerprint,
            settings_digest=settings_digest,
        )
        if (
            progress.plan_digest != intent_digest(intent)
            or progress.settings_digest != settings_digest
        ):
            raise ValueError("Saved destination or execution settings changed; regenerate plan")
        from refinery_ai.workflows.modernization import run_modernization_graph

        return run_modernization_graph(self, intent, progress, store, max_cycles)

    def receipts(self, progress: ModernizationProgress) -> list[StepReceipt]:
        result: list[StepReceipt] = []
        for child in progress.child_ids:
            run_id = MigrationRunStore.find_bound_run(
                self.repository, self.settings.state_directory, child
            )
            if run_id:
                store = MigrationRunStore(self.repository, self.settings.state_directory, run_id)
                manifest = store.load_manifest()
                if (
                    manifest.plan.modernization_plan_id != progress.plan_id
                    or manifest.plan.modernization_plan_digest != progress.plan_digest
                ):
                    raise ValueError("Child run belongs to another modernization destination")
                result.extend(
                    r for r in store.load_steps() if r.plan_digest == manifest.plan_digest
                )
        return result

    def may_reconcile_publication(
        self, progress: ModernizationProgress, evidence: ProjectEvidence
    ) -> bool:
        """Route an interrupted publication to child recovery; do not accept it as verified yet."""
        child = progress.pending_child
        if child is None or not progress.child_ids or progress.child_ids[-1] != child:
            return False
        run_id = MigrationRunStore.find_bound_run(
            self.repository, self.settings.state_directory, child
        )
        if run_id is None:
            return False
        store = MigrationRunStore(self.repository, self.settings.state_directory, run_id)
        manifest, publication = store.load_manifest(), store.load_publication()
        return bool(
            publication
            and manifest.plan.modernization_plan_id == progress.plan_id
            and manifest.plan.modernization_plan_digest == progress.plan_digest
            and publication.starting_revision
            == manifest.starting_revision
            == progress.expected_revision
            and publication.target_revision == evidence.revision
        )

    def accept_owned_publication(
        self, progress: ModernizationProgress, evidence: ProjectEvidence
    ) -> bool:
        if not progress.child_ids:
            return False
        child = progress.child_ids[-1]
        run_id = MigrationRunStore.find_bound_run(
            self.repository, self.settings.state_directory, child
        )
        if not run_id:
            return False
        store = MigrationRunStore(self.repository, self.settings.state_directory, run_id)
        manifest = store.load_manifest()
        publication = store.load_publication()
        if not (
            publication
            and publication.completed
            and manifest.plan.modernization_plan_digest == progress.plan_digest
            and manifest.plan.modernization_plan_id == progress.plan_id
            and publication.starting_revision == manifest.starting_revision
            and progress.expected_revision
            in {publication.starting_revision, publication.target_revision}
            and publication.target_revision == evidence.revision
        ):
            return False
        # Only a clean published tree can advance expected fingerprints after a crash.
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode:
            return False
        ignored = str(self.settings.state_directory).rstrip("/") + "/"
        return all(
            line[:2] == "??" and line[3:].startswith((ignored, ".codegraph/", "graphify-out/"))
            for line in status.stdout.splitlines()
        )
