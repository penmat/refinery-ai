"""Contracts implemented by each migration type."""

from typing import Protocol

from refinery_ai.models import DiscoverySnapshot, RecipeRecommendation, VerificationResult


class MigrationTypeRunner(Protocol):
    def run(
        self,
        snapshot: DiscoverySnapshot,
        step: RecipeRecommendation,
        *,
        apply: bool,
        timeout: int,
        environment: dict[str, str] | None = None,
    ) -> VerificationResult: ...
