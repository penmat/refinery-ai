"""Framework-neutral contracts for reusable repository inspection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from refinery_ai.models import DiscoverySnapshot

InspectionResultT = TypeVar("InspectionResultT")
InspectionResultT_co = TypeVar("InspectionResultT_co", covariant=True)


@dataclass(frozen=True)
class InspectionRequest:
    """Immutable repository facts shared by all inspectors in one run."""

    repository: Path
    snapshot: DiscoverySnapshot


class Inspector(Protocol[InspectionResultT_co]):
    """A reusable evidence collector with effects bounded by its runtime policy."""

    @property
    def name(self) -> str: ...

    def inspect(self, request: InspectionRequest) -> InspectionResultT_co: ...


@dataclass(frozen=True)
class InspectionBundle[InspectionResultT]:
    """Discovery and inspector outputs produced by one sequential run."""

    request: InspectionRequest
    results: dict[str, InspectionResultT]
