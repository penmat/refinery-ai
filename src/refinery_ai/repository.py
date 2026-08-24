"""Refinery-owned boundary for repository operations.

The protocol intentionally exposes only Refinery domain types. Framework-specific
repository environments belong behind this seam.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from refinery_ai.models import DiscoverySnapshot, KnowledgeGraphInfo, VerificationResult


class RepositoryEnvironment(Protocol):
    """Repository capabilities consumed by Refinery orchestration and CLI code."""

    def discover(self, repository: Path) -> DiscoverySnapshot: ...

    def build_knowledge(self, repository: Path) -> KnowledgeGraphInfo: ...

    def query_knowledge(self, graph_path: Path, question: str) -> str: ...

    def verification_command(self, snapshot: DiscoverySnapshot) -> list[str] | None: ...

    def verify(
        self,
        repository: Path,
        command: list[str],
        *,
        timeout: int,
        output_limit: int,
    ) -> VerificationResult: ...

    def dry_run_rewrite(
        self, snapshot: DiscoverySnapshot, recipe: str, *, timeout: int
    ) -> VerificationResult: ...
