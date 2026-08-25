"""Shared utilities for built-in context-graph providers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import JsonValue

from refinery_ai.context_graph.contracts import ContextFragment
from refinery_ai.context_graph.models import ContextEvidence, ContextNode
from refinery_ai.models import DiscoverySnapshot

_IGNORED = {".git", ".gradle", ".idea", ".refinery", "build", "target", "graphify-out"}
_JAVA_TYPE = re.compile(r"\b(?:class|interface|record)\s+(\w+)")
_PACKAGE = re.compile(r"\bpackage\s+([\w.]+)\s*;")
STRING_LITERAL = r'["\']([^"\']+)["\']'


def dependencies(discovery: DiscoverySnapshot | None) -> str:
    """Return normalized dependency coordinates for capability detection."""
    if discovery is None:
        return ""
    return " ".join(
        f"{dependency.group}:{dependency.artifact}" for dependency in discovery.dependencies
    ).casefold()


def files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Find relevant repository files while excluding generated and metadata directories."""
    found: set[Path] = set()
    for pattern in patterns:
        found.update(root.rglob(pattern))
    return sorted(
        path
        for path in found
        if path.is_file() and not _IGNORED.intersection(path.relative_to(root).parts)
    )


def evidence(
    root: Path, path: Path, text: str, position: int = 0, provider: str = ""
) -> ContextEvidence:
    """Create source evidence at the line containing a match."""
    return ContextEvidence(
        provider=provider,
        file=path.relative_to(root),
        line=text.count("\n", 0, position) + 1,
    )


def owner(text: str) -> str | None:
    """Return the context-graph ID of the first declared Java type."""
    type_match = _JAVA_TYPE.search(text)
    if type_match is None:
        return None
    package_match = _PACKAGE.search(text)
    package = package_match.group(1) if package_match else ""
    name = type_match.group(1)
    return f"java:type:{package}.{name}" if package else f"java:type:{name}"


def resource(
    fragment: ContextFragment,
    *,
    node_id: str,
    kind: str,
    name: str,
    evidence: ContextEvidence,
    attributes: Mapping[str, JsonValue] | None = None,
) -> None:
    """Append a resource node to a provider fragment."""
    fragment.nodes.append(
        ContextNode(
            id=node_id,
            kind=kind,
            name=name,
            attributes=dict(attributes or {}),
            sources=[evidence],
        )
    )
