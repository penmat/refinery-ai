"""Refinery-owned boundary for repository operations.

The protocol intentionally exposes only Refinery domain types.  Framework-specific
repository environments (including a future Harness adapter) belong behind this seam.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from refinery_ai.discovery import discover
from refinery_ai.knowledge import GraphifyKnowledgeGraph, KnowledgeGraphProvider
from refinery_ai.models import DiscoverySnapshot, KnowledgeGraphInfo, VerificationResult
from refinery_ai.openrewrite import OpenRewriteAdapter
from refinery_ai.verification import verification_command, verify


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


class LegacyRepositoryEnvironment:
    """Compatibility adapter over the repository tools shipped in Refinery AI v0.1."""

    def __init__(
        self,
        *,
        knowledge: KnowledgeGraphProvider | None = None,
        rewrite: OpenRewriteAdapter | None = None,
        graphify_timeout: int = 900,
        output_limit: int = 20_000,
    ) -> None:
        self.knowledge = knowledge or GraphifyKnowledgeGraph(
            timeout=graphify_timeout, output_limit=output_limit
        )
        self.rewrite = rewrite or OpenRewriteAdapter()

    def discover(self, repository: Path) -> DiscoverySnapshot:
        return discover(repository)

    def build_knowledge(self, repository: Path) -> KnowledgeGraphInfo:
        return self.knowledge.build(repository)

    def query_knowledge(self, graph_path: Path, question: str) -> str:
        query = cast(Callable[[Path, str], str] | None, getattr(self.knowledge, "query", None))
        if query is None:
            raise TypeError("Configured knowledge provider does not support queries")
        return query(graph_path, question)

    def verification_command(self, snapshot: DiscoverySnapshot) -> list[str] | None:
        return verification_command(snapshot)

    def verify(
        self,
        repository: Path,
        command: list[str],
        *,
        timeout: int,
        output_limit: int,
    ) -> VerificationResult:
        return verify(repository, command, timeout=timeout, output_limit=output_limit)

    def dry_run_rewrite(
        self, snapshot: DiscoverySnapshot, recipe: str, *, timeout: int
    ) -> VerificationResult:
        return self.rewrite.dry_run(snapshot, recipe, timeout=timeout)
