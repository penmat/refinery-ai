"""Explainable Phase 1 scoring policy."""

from __future__ import annotations

import hashlib
import json

from refinery_ai.assessment.models import (
    AnalyzerResult,
    AssessmentProfile,
    Category,
    Deduction,
    Score,
    ToolIdentity,
)

SCORING_VERSION = "1.0"
CATEGORY_WEIGHTS: dict[Category, float] = {
    Category.PLATFORM: 0.15,
    Category.DEPENDENCIES: 0.15,
    Category.MAINTAINABILITY: 0.15,
    Category.RISK: 0.15,
    Category.ARCHITECTURE: 0.15,
    Category.TESTS: 0.15,
    Category.BUILD: 0.10,
}


def make_profile(tools: list[ToolIdentity]) -> AssessmentProfile:
    payload = {
        "profileId": "default-v1",
        "scoringVersion": SCORING_VERSION,
        "weights": {key.value: value for key, value in CATEGORY_WEIGHTS.items()},
        "analyzers": [tool.model_dump(mode="json") for tool in tools],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return AssessmentProfile(
        profile_id="default-v1",
        scoring_version=SCORING_VERSION,
        fingerprint=f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}",
        category_weights=CATEGORY_WEIGHTS,
        analyzers=tools,
    )


def score(results: dict[str, AnalyzerResult]) -> Score:
    categories: dict[Category, float | None] = dict.fromkeys(Category)
    deductions: list[Deduction] = []

    platform = results["platform"].metrics
    platform_score = 100.0
    version = _java_major(platform.get("javaVersion"))
    if version is None:
        platform_score -= 35
        deductions.append(
            Deduction(
                category=Category.PLATFORM,
                points=35,
                reason="Java target is not declared",
                metric="platform.javaVersion",
            )
        )
    elif version < 11:
        platform_score -= 45
        deductions.append(
            Deduction(
                category=Category.PLATFORM,
                points=45,
                reason=f"Java {version} predates the current LTS baseline",
                metric="platform.javaVersion",
            )
        )
    elif version < 17:
        platform_score -= 25
        deductions.append(
            Deduction(
                category=Category.PLATFORM,
                points=25,
                reason=f"Java {version} is behind the preferred modernization baseline",
                metric="platform.javaVersion",
            )
        )
    elif version < 21:
        platform_score -= 10
        deductions.append(
            Deduction(
                category=Category.PLATFORM,
                points=10,
                reason=f"Java {version} is behind the latest profile baseline",
                metric="platform.javaVersion",
            )
        )
    if platform.get("usesJavax"):
        platform_score -= 15
        deductions.append(
            Deduction(
                category=Category.PLATFORM,
                points=15,
                reason="Production source imports javax APIs",
                metric="platform.usesJavax",
            )
        )
    categories[Category.PLATFORM] = max(0.0, platform_score)

    source = results["source"].metrics
    rate = float(source.get("largeJavaFilesPerThousand") or 0)
    maintainability_score = max(0.0, 100.0 - min(50.0, rate * 0.5))
    if rate:
        points = 100.0 - maintainability_score
        deductions.append(
            Deduction(
                category=Category.MAINTAINABILITY,
                points=points,
                reason=f"{rate:g} files over 500 lines per 1,000 production Java files",
                metric="source.largeJavaFilesPerThousand",
            )
        )
    categories[Category.MAINTAINABILITY] = maintainability_score

    build = results["build"].metrics
    build_score = 100.0
    if build.get("buildSystem") == "unknown":
        build_score -= 60
        deductions.append(
            Deduction(
                category=Category.BUILD,
                points=60,
                reason="No supported build system was detected",
                metric="build.buildSystem",
            )
        )
    elif not build.get("usesWrapper"):
        build_score -= 15
        deductions.append(
            Deduction(
                category=Category.BUILD,
                points=15,
                reason="The build tool wrapper is absent",
                metric="build.usesWrapper",
            )
        )
    categories[Category.BUILD] = max(0.0, build_score)

    available = {category: value for category, value in categories.items() if value is not None}
    weight = sum(CATEGORY_WEIGHTS[category] for category in available)
    overall = (
        round(
            sum(value * CATEGORY_WEIGHTS[category] for category, value in available.items())
            / weight,
            1,
        )
        if weight
        else None
    )
    return Score(overall=overall, categories=categories, deductions=deductions)


def _java_major(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).split(".")[0])
    except ValueError:
        return None
