"""State and control flow for profile-driven modernization loops."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from refinery_ai.models import MigrationRun, RecipePlan, RunReport, RunStatus
from refinery_ai.profiles import EvidenceMode, ModernizationProfile


class ProfileRunState(BaseModel):
    profile: str
    sonar_dependency_fingerprint: str | None = None
    completed: bool = False
    cycles: int = Field(default=0, ge=0)


class ProfileRunResult(BaseModel):
    profile: str
    status: RunStatus
    cycles: int
    message: str


@dataclass(frozen=True)
class VerifiedCheckpoint:
    revision: str
    java: int


class ProfileRunStore:
    def __init__(
        self,
        repository: Path,
        profile: str,
        state_directory: Path = Path(".refinery"),
    ) -> None:
        self.path = (
            repository.expanduser().resolve() / state_directory / "profile-runs" / f"{profile}.json"
        )
        self.profile = profile

    def load(self) -> ProfileRunState:
        if not self.path.is_file():
            return ProfileRunState(profile=self.profile)
        return ProfileRunState.model_validate_json(self.path.read_text())

    def save(self, state: ProfileRunState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(state.model_dump_json(indent=2) + "\n")
        temporary.replace(self.path)


def dependency_inputs_fingerprint(repository: Path) -> str:
    """Hash files capable of changing the resolved JVM dependency graph."""

    root = repository.expanduser().resolve()
    exact_names = {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
        "libs.versions.toml",
        "gradle.lockfile",
        "gradle-wrapper.properties",
    }
    ignored = {".git", ".gradle", ".idea", ".refinery", ".codegraph", "build", "target"}
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ignored.intersection(path.relative_to(root).parts):
            continue
        if path.name not in exact_names and not path.name.endswith(".lockfile"):
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


class ProfileRunner:
    """Repeat analysis, planning, and verified migration until a profile is satisfied."""

    def __init__(
        self,
        repository: Path,
        profile: ModernizationProfile,
        *,
        analyze: Callable[[bool], bool],
        plan: Callable[[], RunReport],
        migrate: Callable[[str, VerifiedCheckpoint | None], MigrationRun],
        progress: Callable[[str], None] | None = None,
        max_cycles: int = 20,
        state_directory: Path = Path(".refinery"),
    ) -> None:
        self.repository = repository.expanduser().resolve()
        self.profile = profile
        self.analyze = analyze
        self.plan = plan
        self.migrate = migrate
        self.progress = progress or (lambda message: None)
        self.max_cycles = max_cycles
        self.store = ProfileRunStore(self.repository, profile.name, state_directory)

    def run(self) -> ProfileRunResult:
        state = self.store.load()
        previous_recipes: tuple[str, ...] | None = None
        verified_checkpoint: VerifiedCheckpoint | None = None
        for cycle in range(1, self.max_cycles + 1):
            dependency_fingerprint = dependency_inputs_fingerprint(self.repository)
            sonar_enabled = self.profile.evidence.sonarqube is not EvidenceMode.OFF
            run_sonar = sonar_enabled and (
                state.sonar_dependency_fingerprint != dependency_fingerprint
            )
            sonar_note = (
                "without SonarQube"
                if not sonar_enabled
                else "with SonarQube"
                if run_sonar
                else "reusing SonarQube evidence"
            )
            self.progress(f"Cycle {cycle}: analyzing ({sonar_note}) ...")
            sonar_available = self.analyze(run_sonar)
            if sonar_enabled and not run_sonar and not sonar_available:
                self.progress(
                    "Retained SonarQube evidence is unavailable; running a fresh scan ..."
                )
                run_sonar = True
                sonar_available = self.analyze(True)
            if run_sonar and sonar_available:
                state.sonar_dependency_fingerprint = dependency_fingerprint
            state.cycles += 1
            state.completed = False
            self.store.save(state)

            self.progress(f"Cycle {cycle}: planning for {self.profile.name} ...")
            report = self.plan()
            if not isinstance(report.plan, RecipePlan):
                raise RuntimeError("Profile planner did not return recipe recommendations")
            recipes = tuple(item.recipe for item in report.plan.recommendations)
            if not recipes:
                target_gaps = self.profile.unresolved_targets(report.snapshot)
                if target_gaps:
                    unresolved = ", ".join(
                        f"{gap.target} {gap.current_version or 'unknown'} -> "
                        f"{gap.target_version}"
                        for gap in target_gaps
                    )
                    return self._stalled(
                        state,
                        cycle,
                        "the planner returned no migrations while profile targets remain "
                        f"unsatisfied ({unresolved})",
                    )
                state.completed = True
                self.store.save(state)
                return ProfileRunResult(
                    profile=self.profile.name,
                    status=RunStatus.COMPLETED,
                    cycles=cycle,
                    message="Profile complete; the planner found no remaining migrations.",
                )
            if recipes == previous_recipes:
                return self._stalled(state, cycle, "the planner repeated the same migration plan")
            previous_recipes = recipes

            self.progress(f"Cycle {cycle}: running {len(recipes)} migration(s) ...")
            migration = self.migrate(report.run_id, verified_checkpoint)
            if migration.status is not RunStatus.COMPLETED:
                return ProfileRunResult(
                    profile=self.profile.name,
                    status=migration.status,
                    cycles=cycle,
                    message="Profile run stopped because a migration failed.",
                )
            if migration.published_revision is None:
                return self._stalled(state, cycle, "the migration produced no published changes")
            verified_checkpoint = (
                VerifiedCheckpoint(
                    revision=migration.published_revision,
                    java=migration.verified_java,
                )
                if migration.verified_java is not None
                else None
            )

        return self._stalled(state, self.max_cycles, "the maximum cycle count was reached")

    def _stalled(self, state: ProfileRunState, cycle: int, reason: str) -> ProfileRunResult:
        self.store.save(state)
        return ProfileRunResult(
            profile=self.profile.name,
            status=RunStatus.NEEDS_REVIEW,
            cycles=cycle,
            message=f"Profile run stopped: {reason}.",
        )
