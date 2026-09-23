"""Deterministic Maven property target finalizers."""

from __future__ import annotations

import re
from pathlib import Path
from time import monotonic

from refinery_ai.models import DiscoverySnapshot, RecipeRecommendation, VerificationResult

SET_SPRING_BOOT_VERSION_PREFIX = "refinery.maven.set-spring-boot-version."


class MavenPropertyMigrationRunner:
    def run(
        self,
        snapshot: DiscoverySnapshot,
        step: RecipeRecommendation,
        *,
        apply: bool,
        timeout: int,
        environment: dict[str, str] | None = None,
    ) -> VerificationResult:
        del timeout, environment
        started = monotonic()
        if not step.recipe.startswith(SET_SPRING_BOOT_VERSION_PREFIX):
            return _result(1, started, stderr=f"Unknown Maven property migration: {step.recipe}")
        version = step.recipe.removeprefix(SET_SPRING_BOOT_VERSION_PREFIX)
        if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
            return _result(1, started, stderr=f"Invalid Spring Boot target version: {version}")

        repository = Path(snapshot.repository).resolve()
        changed: list[str] = []
        pattern = re.compile(
            r"(<spring-boot\.version>\s*)[^<]+(\s*</spring-boot\.version>)"
        )
        for pom in sorted(repository.rglob("pom.xml")):
            relative = pom.relative_to(repository)
            if ".refinery" in relative.parts or "target" in relative.parts:
                continue
            content = pom.read_text(errors="replace")
            updated = pattern.sub(rf"\g<1>{version}\g<2>", content)
            if updated == content:
                continue
            if apply:
                pom.write_text(updated)
            changed.append(str(relative))
        action = "Updated" if apply else "Would update"
        return _result(0, started, stdout=f"{action}: " + ", ".join(changed))


def _result(
    exit_code: int, started: float, *, stdout: str = "", stderr: str = ""
) -> VerificationResult:
    return VerificationResult(
        command=["refinery", "set-spring-boot-version"],
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=monotonic() - started,
    )
