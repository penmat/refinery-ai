"""Built-in Phase 1 analyzers using repository-local evidence only."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Protocol

from refinery_ai.assessment.models import AnalyzerResult, AnalyzerStatus, ToolIdentity
from refinery_ai.models import BuildSystem, DiscoverySnapshot

ANALYZER_VERSION = "1.0"
_CONFIGURATION = "phase1-local-v1"
_CONFIGURATION_HASH = hashlib.sha256(_CONFIGURATION.encode()).hexdigest()
_IGNORED = {".git", ".gradle", ".idea", ".refinery", "build", "target", "graphify-out"}


class Analyzer(Protocol):
    name: str

    def analyze(self, repository: Path, snapshot: DiscoverySnapshot) -> AnalyzerResult: ...


def identity(name: str) -> ToolIdentity:
    return ToolIdentity(name=name, version=ANALYZER_VERSION, configuration_hash=_CONFIGURATION_HASH)


def _result(
    name: str, started: float, metrics: dict[str, int | float | str | bool | None]
) -> AnalyzerResult:
    return AnalyzerResult(
        analyzer=name,
        status=AnalyzerStatus.COMPLETED,
        completeness=1,
        duration_seconds=time.monotonic() - started,
        tool=identity(name),
        metrics=metrics,
    )


class DiscoveryAnalyzer:
    name = "discovery"

    def analyze(self, repository: Path, snapshot: DiscoverySnapshot) -> AnalyzerResult:
        started = time.monotonic()
        return _result(
            self.name,
            started,
            {
                "moduleCount": len(snapshot.modules),
                "dependencyCount": len(snapshot.dependencies),
                "frameworkCount": len(snapshot.frameworks),
            },
        )


class PlatformAnalyzer:
    name = "platform"

    def analyze(self, repository: Path, snapshot: DiscoverySnapshot) -> AnalyzerResult:
        started = time.monotonic()
        java_files = _java_files(repository)
        uses_javax = False
        uses_jakarta = False
        for path in java_files:
            try:
                content = path.read_text(errors="replace")
            except OSError:
                continue
            uses_javax = uses_javax or bool(re.search(r"^import\s+javax\.", content, re.MULTILINE))
            uses_jakarta = uses_jakarta or bool(
                re.search(r"^import\s+jakarta\.", content, re.MULTILINE)
            )
        return _result(
            self.name,
            started,
            {
                "javaVersion": snapshot.java_version,
                "usesJavax": uses_javax,
                "usesJakarta": uses_jakarta,
            },
        )


class BuildAnalyzer:
    name = "build"

    def analyze(self, repository: Path, snapshot: DiscoverySnapshot) -> AnalyzerResult:
        started = time.monotonic()
        wrapper = (
            (repository / "mvnw").is_file()
            if snapshot.build_system is BuildSystem.MAVEN
            else (repository / "gradlew").is_file()
            if snapshot.build_system is BuildSystem.GRADLE
            else False
        )
        return _result(
            self.name,
            started,
            {
                "buildSystem": snapshot.build_system.value,
                "buildFileCount": len(snapshot.build_files),
                "usesWrapper": wrapper,
            },
        )


class SourceAnalyzer:
    name = "source"

    def analyze(self, repository: Path, snapshot: DiscoverySnapshot) -> AnalyzerResult:
        started = time.monotonic()
        files = _java_files(repository)
        production = [path for path in files if "src/test" not in path.as_posix()]
        test = [path for path in files if "src/test" in path.as_posix()]
        lines = 0
        nonblank = 0
        large_files = 0
        for path in production:
            try:
                content_lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            lines += len(content_lines)
            nonblank += sum(bool(line.strip()) for line in content_lines)
            large_files += len(content_lines) > 500
        return _result(
            self.name,
            started,
            {
                "productionJavaFiles": len(production),
                "testJavaFiles": len(test),
                "productionLines": lines,
                "productionNonblankLines": nonblank,
                "largeJavaFiles": large_files,
                "largeJavaFilesPerThousand": round(large_files * 1000 / len(production), 3)
                if production
                else 0.0,
            },
        )


def _java_files(repository: Path) -> list[Path]:
    return [
        path
        for path in sorted(repository.rglob("*.java"))
        if not _IGNORED.intersection(path.relative_to(repository).parts)
    ]


BUILT_IN_ANALYZERS: tuple[Analyzer, ...] = (
    DiscoveryAnalyzer(),
    PlatformAnalyzer(),
    BuildAnalyzer(),
    SourceAnalyzer(),
)
