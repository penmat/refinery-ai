"""State and control flow for profile-driven modernization loops."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from refinery_ai.models import MigrationRun, RunReport, RunStatus
from refinery_ai.profiles import ModernizationProfile


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
        from refinery_ai.workflows.profile import run_profile_graph

        return run_profile_graph(self)

    def _stalled(self, state: ProfileRunState, cycle: int, reason: str) -> ProfileRunResult:
        self.store.save(state)
        return ProfileRunResult(
            profile=self.profile.name,
            status=RunStatus.NEEDS_REVIEW,
            cycles=cycle,
            message=f"Profile run stopped: {reason}.",
        )
