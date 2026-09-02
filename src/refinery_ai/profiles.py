"""Named modernization profiles that bound planner recommendations."""

from __future__ import annotations

from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from refinery_ai.models import RecipePlan


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
    include_recipes: list[str] = Field(default_factory=list)
    exclude_recipes: list[str] = Field(default_factory=list)
    evidence: ProfileEvidencePolicy = Field(default_factory=ProfileEvidencePolicy)

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
