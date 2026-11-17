"""Explicit analyzer and transformation scope for modernization modes."""

from dataclasses import dataclass

from refinery_ai.modernization.models import ModernizationMode


@dataclass(frozen=True)
class ModePolicy:
    mode: ModernizationMode
    dependencies: bool
    security: bool
    sonarqube: bool
    codegraph: bool
    categories: frozenset[str]


def mode_policy(mode: ModernizationMode | str) -> ModePolicy:
    mode = ModernizationMode(mode)
    minimal = mode is ModernizationMode.MINIMAL
    full = mode is ModernizationMode.FULL
    return ModePolicy(
        mode,
        not minimal,
        not minimal,
        full,
        full,
        frozenset(
            {"compatibility"} if minimal else {"compatibility", "dependency", "security", "source"}
        ),
    )
