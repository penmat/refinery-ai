"""Sequential execution of reusable repository inspectors."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from refinery_ai.discovery import discover
from refinery_ai.inspectors.contracts import (
    InspectionBundle,
    InspectionRequest,
    InspectionResultT,
)
from refinery_ai.inspectors.registry import InspectorRegistry
from refinery_ai.models import DiscoverySnapshot


class InspectionRunner[InspectionResultT]:
    """Run selected inspectors in registry order against one discovery snapshot."""

    def __init__(
        self,
        registry: InspectorRegistry[InspectionResultT],
        *,
        discoverer: Callable[[Path], DiscoverySnapshot] = discover,
    ) -> None:
        self.registry = registry
        self.discoverer = discoverer

    def inspect(
        self,
        repository: Path,
        *,
        names: Iterable[str] | None = None,
        snapshot: DiscoverySnapshot | None = None,
    ) -> InspectionBundle[InspectionResultT]:
        root = repository.expanduser().resolve()
        selected_snapshot = snapshot or self.discoverer(root)
        if selected_snapshot.repository.resolve() != root:
            raise ValueError("Discovery snapshot does not belong to the inspected repository")
        request = InspectionRequest(repository=root, snapshot=selected_snapshot)
        results: dict[str, InspectionResultT] = {}
        for inspector in self.registry.select(names):
            results[inspector.name] = inspector.inspect(request)
        return InspectionBundle(request=request, results=results)
