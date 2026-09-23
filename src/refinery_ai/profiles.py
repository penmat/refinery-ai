"""Named modernization profiles that bound planner recommendations."""

from __future__ import annotations

from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from refinery_ai.models import DiscoverySnapshot, RecipePlan


def _java_version(value: str | None) -> int | None:
    if value is None:
        return None
    parts = value.strip().split(".")
    try:
        return int(parts[1] if parts[0] == "1" and len(parts) > 1 else parts[0])
    except ValueError:
        return None


def _major_minor(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    parts = value.strip().split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


class ProfileTargetGap(BaseModel):
    target: str
    current_version: str | None
    target_version: str


class EvidenceMode(StrEnum):
    OFF = "off"
    AUTO = "auto"
    REQUIRED = "required"


class ProfileEvidencePolicy(BaseModel):
    context_graph: EvidenceMode = EvidenceMode.AUTO
    sonarqube: EvidenceMode = EvidenceMode.AUTO


class ModernizationProfile(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    description: str
    goal: str
    target_java: int | None = Field(default=None, ge=8)
    target_spring_boot: str | None = Field(default=None, pattern=r"^\d+\.\d+$")
    target_spring_boot_version: str | None = Field(
        default=None, pattern=r"^\d+\.\d+\.\d+$"
    )
    target_recipes: dict[str, str] = Field(default_factory=dict)
    include_recipes: list[str] = Field(default_factory=list)
    exclude_recipes: list[str] = Field(default_factory=list)
    evidence: ProfileEvidencePolicy = Field(default_factory=ProfileEvidencePolicy)

    def unresolved_targets(self, snapshot: DiscoverySnapshot) -> list[ProfileTargetGap]:
        """Return measurable profile targets the current discovery has not reached."""

        gaps: list[ProfileTargetGap] = []
        current_java = _java_version(snapshot.java_version)
        if self.target_java is not None and (
            current_java is None or current_java < self.target_java
        ):
            gaps.append(
                ProfileTargetGap(
                    target="java",
                    current_version=snapshot.java_version,
                    target_version=str(self.target_java),
                )
            )
        current_boot = _major_minor(snapshot.spring_boot_version)
        target_boot = _major_minor(self.target_spring_boot)
        if target_boot is not None and (current_boot is None or current_boot < target_boot):
            gaps.append(
                ProfileTargetGap(
                    target="spring_boot",
                    current_version=snapshot.spring_boot_version,
                    target_version=self.target_spring_boot or "",
                )
            )
        return gaps

    def permits(self, recipe: str) -> bool:
        included = not self.include_recipes or any(
            fnmatchcase(recipe, pattern) for pattern in self.include_recipes
        )
        excluded = any(fnmatchcase(recipe, pattern) for pattern in self.exclude_recipes)
        return included and not excluded

    def filter_plan(self, plan: RecipePlan) -> RecipePlan:
        retained = [item for item in plan.recommendations if self.permits(item.recipe)]
        retained_recipes = {item.recipe for item in retained}
        return RecipePlan(
            recommendations=[
                item.model_copy(
                    update={
                        "depends_on": [
                            dependency
                            for dependency in item.depends_on
                            if dependency in retained_recipes
                        ]
                    }
                )
                for item in retained
            ]
        )


def load_profile(name: str) -> ModernizationProfile:
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789._-"
    if not name or any(character not in allowed for character in name):
        raise ValueError(f"Invalid profile name: {name}")
    path = Path(__file__).with_name("profiles") / f"{name}.yaml"
    if not path.is_file():
        available = ", ".join(available_profiles()) or "none"
        raise FileNotFoundError(f"Unknown profile '{name}'. Available profiles: {available}")
    try:
        payload = yaml.safe_load(path.read_text())
        profile = ModernizationProfile.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
        raise ValueError(f"Unable to load modernization profile {name}: {error}") from error
    if profile.name != name:
        raise ValueError(
            f"Profile filename '{name}' does not match its declared name '{profile.name}'"
        )
    return profile


def available_profiles() -> list[str]:
    root = Path(__file__).with_name("profiles")
    return sorted(path.stem for path in root.glob("*.yaml"))
