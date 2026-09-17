"""Shared migration-repair contracts and result construction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from refinery_ai.models import RecipeRecommendation, VerificationResult


@runtime_checkable
class MigrationRepairer(Protocol):
    def repair(
        self,
        repository: Path,
        step: RecipeRecommendation,
        failure: VerificationResult,
    ) -> VerificationResult: ...


@dataclass(frozen=True)
class MigrationRepairStage:
    """One independently budgeted migration-repair strategy."""

    name: str
    repairer: MigrationRepairer
    max_attempts: int
    uses_model: bool = False


class MigrationRepairChain:
    """Try repair strategies in order until one produces a patch."""

    def __init__(self, *repairers: MigrationRepairer) -> None:
        self.repairers = repairers

    def repair(
        self,
        repository: Path,
        step: RecipeRecommendation,
        failure: VerificationResult,
    ) -> VerificationResult:
        result = repair_result(1, "No migration repair matched the failure")
        for repairer in self.repairers:
            result = repairer.repair(repository, step, failure)
            if result.green:
                return result
        return result


def repair_result(exit_code: int, stderr: str = "", stdout: str = "") -> VerificationResult:
    return VerificationResult(
        command=["refinery", "ai-repair"],
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0,
    )
