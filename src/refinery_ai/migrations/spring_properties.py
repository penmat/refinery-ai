"""Deterministic Spring Boot compatibility properties migrations."""

from __future__ import annotations

from pathlib import Path
from time import monotonic

from refinery_ai.models import DiscoverySnapshot, RecipeRecommendation, VerificationResult

DEFER_DATASOURCE_INITIALIZATION_RECIPE = "refinery.defer-datasource-initialization"
_SETTING = "spring.jpa.defer-datasource-initialization=true"


class SpringPropertiesMigrationRunner:
    def run(
        self,
        snapshot: DiscoverySnapshot,
        step: RecipeRecommendation,
        *,
        apply: bool,
        timeout: int,
    ) -> VerificationResult:
        del step, timeout
        started = monotonic()
        repository = Path(snapshot.repository).resolve()
        changed: list[str] = []
        for relative in snapshot.deferred_datasource_initialization_properties:
            path = (repository / relative).resolve()
            if not path.is_relative_to(repository):
                return _result(1, started, stderr=f"Unsafe properties file: {relative}")
            content = path.read_text(errors="replace") if path.is_file() else ""
            if _SETTING in content:
                continue
            if apply:
                separator = "" if not content or content.endswith("\n") else "\n"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{content}{separator}{_SETTING}\n")
            changed.append(str(relative))
        action = "Updated" if apply else "Would update"
        return _result(0, started, stdout=f"{action}: " + ", ".join(changed))


def _result(
    exit_code: int, started: float, *, stdout: str = "", stderr: str = ""
) -> VerificationResult:
    return VerificationResult(
        command=["refinery", "defer-datasource-initialization"],
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=monotonic() - started,
    )
