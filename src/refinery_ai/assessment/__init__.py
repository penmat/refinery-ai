"""Deterministic repository assessment capability."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from refinery_ai.assessment.engine import AssessmentEngine

__all__ = ["AssessmentEngine"]


def __getattr__(name: str) -> type["AssessmentEngine"]:
    # Inspector contracts import assessment.models; eager engine loading creates a cycle.
    if name == "AssessmentEngine":
        from refinery_ai.assessment.engine import AssessmentEngine

        return AssessmentEngine
    raise AttributeError(name)
