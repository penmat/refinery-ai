"""Typed, framework-neutral contracts for bounded specialist investigations."""

from __future__ import annotations

from pydantic import BaseModel


class SpecialistObservation(BaseModel):
    """One evidence-linked specialist observation."""

    key: str
    value: str
    evidence_paths: tuple[str, ...] = ()


class BuildRuntimeSpecialistResult(BaseModel):
    """Read-only build/runtime specialist output used by the H6 pilot."""

    observations: tuple[SpecialistObservation, ...] = ()
    unresolved_questions: tuple[str, ...] = ()


class SpecialistConflict(BaseModel):
    """Conflicting values retained for deterministic human-visible resolution."""

    key: str
    values: tuple[str, ...]


class MergedSpecialistResults(BaseModel):
    """Deterministic merge product; conflicts are never silently overwritten."""

    observations: tuple[SpecialistObservation, ...]
    conflicts: tuple[SpecialistConflict, ...]
    unresolved_questions: tuple[str, ...]


def merge_specialist_results(
    results: list[BuildRuntimeSpecialistResult],
) -> MergedSpecialistResults:
    """Deduplicate equivalent observations and report contradictory values."""

    by_key: dict[str, dict[str, set[str]]] = {}
    questions: set[str] = set()
    for result in results:
        questions.update(result.unresolved_questions)
        for observation in result.observations:
            values = by_key.setdefault(observation.key, {})
            values.setdefault(observation.value, set()).update(observation.evidence_paths)

    observations: list[SpecialistObservation] = []
    conflicts: list[SpecialistConflict] = []
    for key, values in sorted(by_key.items()):
        if len(values) > 1:
            conflicts.append(SpecialistConflict(key=key, values=tuple(sorted(values))))
            continue
        value, paths = next(iter(values.items()))
        observations.append(
            SpecialistObservation(key=key, value=value, evidence_paths=tuple(sorted(paths)))
        )
    return MergedSpecialistResults(
        observations=tuple(observations),
        conflicts=tuple(conflicts),
        unresolved_questions=tuple(sorted(questions)),
    )
