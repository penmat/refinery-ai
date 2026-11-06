"""Deterministic migration plan orchestration and verified publication."""

from __future__ import annotations

import re
from collections.abc import Callable

from refinery_ai.jdks import (
    JdkResolver,
)
from refinery_ai.migrations.contracts import MigrationTypeRunner
from refinery_ai.migrations.events import MigrationEvent
from refinery_ai.migrations.execution_state import MigrationPolicy
from refinery_ai.migrations.maven_property import (
    SET_SPRING_BOOT_VERSION_PREFIX,
    MavenPropertyMigrationRunner,
)
from refinery_ai.migrations.openrewrite import OpenRewriteMigrationRunner
from refinery_ai.migrations.repair import (
    MigrationRepairer,
    MigrationRepairStage,
)
from refinery_ai.migrations.spring_properties import (
    DEFER_DATASOURCE_INITIALIZATION_RECIPE,
    SpringPropertiesMigrationRunner,
)
from refinery_ai.migrations.utf8 import (
    UTF8_APPLICATION_PROPERTIES_RECIPE,
    Utf8PropertiesMigrationRunner,
)
from refinery_ai.migrations.worktrees import GitWorktreeManager
from refinery_ai.models import (
    DiscoverySnapshot,
    ExecutionType,
    MigrationRun,
    VerificationResult,
)
from refinery_ai.planning_artifacts import MigrationPlanStore
from refinery_ai.verification import verification_command, verify


def _reactor_success_count(diagnostic: str) -> int:
    """Count modules Maven reports as successful before the failing module."""

    return len(re.findall(r"(?m)^\[INFO\]\s+.+?\.{3,}\s+SUCCESS(?:\s|$)", diagnostic))


def _unique_compilation_errors(diagnostic: str) -> int:
    """Count distinct compiler diagnostics without Maven's repeated summary copy."""

    errors: set[str] = set()
    source_error = re.compile(r"^\[ERROR\]\s+(.+?\.(?:java|kt|groovy)):\[(\d+)(?:,\d+)?\]\s+(.+)$")
    for raw_line in diagnostic.splitlines():
        line = raw_line.strip()
        match = source_error.match(line)
        if match:
            # Worktree roots may vary between attempts, while the source suffix,
            # line and diagnostic identify the same underlying compiler failure.
            source = match.group(1).replace("\\", "/").rsplit("/src/", 1)[-1]
            errors.add(f"{source}:{match.group(2)}:{match.group(3)}")
    if errors:
        return len(errors)

    markers = (
        "cannot find symbol",
        "package does not exist",
        "incompatible types:",
    )
    fallback = {
        line.strip()
        for line in diagnostic.splitlines()
        if any(marker in line for marker in markers)
    }
    return len(fallback)


def _failure_score(result: VerificationResult) -> tuple[int, int, int]:
    """Rank red verification results so model repairs can be kept only when they improve."""

    if result.green:
        return (0, 0, 0)
    diagnostic = f"{result.stdout}\n{result.stderr}"
    compilation_errors = _unique_compilation_errors(diagnostic)
    if compilation_errors:
        return (3, -_reactor_success_count(diagnostic), compilation_errors)
    test_summaries = re.findall(
        r"Tests run:\s*\d+,\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)", diagnostic
    )
    if test_summaries:
        failures, errors = test_summaries[-1]
        return (2, 0, int(failures) + int(errors))
    return (4, -_reactor_success_count(diagnostic), 1)


class MigrationRunner:
    """Load a persisted plan and dispatch each step by its execution type."""

    def __init__(
        self,
        store: MigrationPlanStore,
        *,
        runners: dict[ExecutionType, MigrationTypeRunner] | None = None,
        timeout: int = 900,
        output_limit: int = 20_000,
        build_command: Callable[[DiscoverySnapshot], list[str] | None] = verification_command,
        build_verifier: Callable[..., VerificationResult] = verify,
        progress: Callable[[str], None] | None = None,
        worktree_factory: type[GitWorktreeManager] = GitWorktreeManager,
        repairer: MigrationRepairer | None = None,
        max_repair_attempts: int = 1,
        repair_stages: tuple[MigrationRepairStage, ...] | None = None,
        jdk_resolver: JdkResolver | None = None,
        policy: MigrationPolicy | None = None,
        event_observer: Callable[[MigrationEvent], None] | None = None,
    ) -> None:
        self.event_observer = event_observer
        self.policy = policy
        self.store = store
        self.runners = runners or {
            ExecutionType.OPENREWRITE: OpenRewriteMigrationRunner(),
            ExecutionType.UTF8: Utf8PropertiesMigrationRunner(),
            ExecutionType.SPRING_PROPERTIES: SpringPropertiesMigrationRunner(),
            ExecutionType.MAVEN_PROPERTY: MavenPropertyMigrationRunner(),
        }
        self.timeout = timeout
        self.output_limit = output_limit
        self.build_command = build_command
        self.build_verifier = build_verifier
        self.progress = progress or (lambda message: None)
        self.worktree_factory = worktree_factory
        self.repairer = repairer
        self.max_repair_attempts = max_repair_attempts
        self.repair_stages = repair_stages
        self.jdk_resolver = jdk_resolver
        self._jdk_environments: dict[int, dict[str, str]] = {}

    def run(
        self,
        plan_id: str | None = None,
        *,
        apply: bool = False,
        publish: bool = True,
        expected_profile: str | None = None,
        trusted_baseline_revision: str | None = None,
        trusted_baseline_java: int | None = None,
        resume_run_id: str | None = None,
        resume_bound_run: bool = False,
    ) -> MigrationRun:
        from refinery_ai.migrations.execution_state import MigrationRequest
        from refinery_ai.workflows.migration import run_migration_graph

        return run_migration_graph(
            self,
            MigrationRequest(
                plan_id=plan_id,
                apply=apply,
                publish=publish,
                expected_profile=expected_profile,
                trusted_baseline_revision=trusted_baseline_revision,
                trusted_baseline_java=trusted_baseline_java,
                resume_run_id=resume_run_id,
                resume_bound_run=resume_bound_run,
            ),
        )

    def _jdk_environment(self, major: int | None) -> dict[str, str] | None:
        if self.jdk_resolver is None:
            return None
        if major is None:
            return None
        if major not in self._jdk_environments:
            self._jdk_environments[major] = self.jdk_resolver.environment(major) or {}
        return self._jdk_environments[major]


def migration_display_name(recipe: str) -> str:
    """Turn a fully qualified recipe class into a concise progress label."""

    if recipe == UTF8_APPLICATION_PROPERTIES_RECIPE:
        return "Convert application properties to UTF-8"
    if recipe == DEFER_DATASOURCE_INITIALIZATION_RECIPE:
        return "Defer datasource initialization"
    if recipe.startswith(SET_SPRING_BOOT_VERSION_PREFIX):
        version = recipe.removeprefix(SET_SPRING_BOOT_VERSION_PREFIX)
        return f"Set Spring Boot version to {version}"
    name = recipe.rsplit(".", maxsplit=1)[-1]
    java_upgrade = re.fullmatch(r"Java(\d+)(?:to|To)Java(\d+)", name)
    if java_upgrade:
        return f"Java {java_upgrade.group(1)} → Java {java_upgrade.group(2)}"
    upgrade = re.fullmatch(r"UpgradeToJava(\d+)", name)
    if upgrade:
        return f"Upgrade to Java {upgrade.group(1)}"
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return words.replace(" To ", " → ")
