"""Ordered registration and selection of reusable inspectors."""

from __future__ import annotations

from collections.abc import Iterable

from refinery_ai.inspectors.contracts import InspectionResultT, Inspector


class InspectorRegistry[InspectionResultT]:
    """A deterministic inspector catalog keyed by stable inspector name."""

    def __init__(self, inspectors: Iterable[Inspector[InspectionResultT]] = ()) -> None:
        self._inspectors: dict[str, Inspector[InspectionResultT]] = {}
        for inspector in inspectors:
            self.register(inspector)

    def register(self, inspector: Inspector[InspectionResultT]) -> None:
        if inspector.name in self._inspectors:
            raise ValueError(f"Inspector is already registered: {inspector.name}")
        self._inspectors[inspector.name] = inspector

    def select(
        self, names: Iterable[str] | None = None
    ) -> tuple[Inspector[InspectionResultT], ...]:
        if names is None:
            return tuple(self._inspectors.values())
        selected: list[Inspector[InspectionResultT]] = []
        for name in names:
            try:
                selected.append(self._inspectors[name])
            except KeyError as error:
                raise KeyError(f"Unknown inspector: {name}") from error
        return tuple(selected)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._inspectors)
