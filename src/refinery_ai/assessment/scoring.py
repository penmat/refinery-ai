"""Explainable Phase 1 scoring policy."""

from __future__ import annotations

import hashlib
import json

from refinery_ai.assessment.models import (
    AnalyzerResult,
    AnalyzerStatus,
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

    dependencies = results.get("dependencies")
    if dependencies and dependencies.status is AnalyzerStatus.COMPLETED:
        critical = int(dependencies.metrics.get("criticalVulnerabilityCount") or 0)
        high = int(dependencies.metrics.get("highVulnerabilityCount") or 0)
        medium = int(dependencies.metrics.get("mediumVulnerabilityCount") or 0)
        low = int(dependencies.metrics.get("lowVulnerabilityCount") or 0)
        unknown = int(dependencies.metrics.get("unknownVulnerabilityCount") or 0)
        dependency_score = 100.0
        for count, rate, cap, label, metric in (
            (critical, 20.0, 60.0, "critical", "criticalVulnerabilityCount"),
            (high, 10.0, 40.0, "high", "highVulnerabilityCount"),
            (medium, 4.0, 24.0, "medium", "mediumVulnerabilityCount"),
            (low, 1.0, 10.0, "low", "lowVulnerabilityCount"),
            (unknown, 4.0, 20.0, "unknown-severity", "unknownVulnerabilityCount"),
        ):
            points = min(cap, count * rate)
            dependency_score -= points
            if points:
                deductions.append(
                    Deduction(
                        category=Category.DEPENDENCIES,
                        points=points,
                        reason=f"OSV reports {count} {label} vulnerable dependency findings",
                        metric=f"dependencies.{metric}",
                    )
                )
        categories[Category.DEPENDENCIES] = max(0.0, dependency_score)

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

    sonar = results.get("sonarqube")
    if sonar and sonar.status in {AnalyzerStatus.COMPLETED, AnalyzerStatus.PARTIAL}:
        sonar_metrics = sonar.metrics
        ncloc = max(float(sonar_metrics.get("ncloc") or 0), 1)
        duplication = _float(sonar_metrics.get("duplicatedLinesDensity"))
        if duplication is not None:
            points = min(25.0, duplication * 1.25)
            maintainability_score -= points
            if points:
                deductions.append(
                    Deduction(
                        category=Category.MAINTAINABILITY,
                        points=points,
                        reason=f"SonarQube reports {duplication:g}% duplicated lines",
                        metric="sonarqube.duplicatedLinesDensity",
                    )
                )
        cognitive = _float(sonar_metrics.get("cognitiveComplexity"))
        if cognitive is not None:
            cognitive_per_thousand = cognitive * 1000 / ncloc
            points = min(30.0, cognitive_per_thousand * 0.3)
            maintainability_score -= points
            if points:
                deductions.append(
                    Deduction(
                        category=Category.MAINTAINABILITY,
                        points=points,
                        reason=(
                            f"SonarQube cognitive complexity is {cognitive_per_thousand:.1f} "
                            "per 1,000 lines"
                        ),
                        metric="sonarqube.cognitiveComplexity",
                    )
                )
        categories[Category.MAINTAINABILITY] = max(0.0, maintainability_score)

        risk_findings = [finding for finding in sonar.findings if finding.category is Category.RISK]
        blockers = sum(finding.severity == "blocker" for finding in risk_findings)
        high = sum(finding.severity in {"high", "critical"} for finding in risk_findings)
        medium = sum(finding.severity in {"medium", "major"} for finding in risk_findings)
        blocker_points = min(40.0, blockers * 10.0)
        high_points = min(40.0, high * 4.0)
        medium_points = min(20.0, medium * 1.0)
        risk_score = max(0.0, 100.0 - blocker_points - high_points - medium_points)
        for points, count, label in (
            (blocker_points, blockers, "blocker"),
            (high_points, high, "high-severity"),
            (medium_points, medium, "medium-severity"),
        ):
            if points:
                deductions.append(
                    Deduction(
                        category=Category.RISK,
                        points=points,
                        reason=f"SonarQube reports {count} {label} risk findings",
                        metric="sonarqube.findings",
                    )
                )
        categories[Category.RISK] = risk_score

        coverage = _float(sonar_metrics.get("coverage"))
        branch_coverage = _float(sonar_metrics.get("branchCoverage"))
        if coverage is not None:
            test_score = (
                coverage if branch_coverage is None else coverage * 0.6 + branch_coverage * 0.4
            )
            test_category_score = max(0.0, min(100.0, round(test_score, 1)))
            categories[Category.TESTS] = test_category_score
            points = 100.0 - test_category_score
            if points:
                deductions.append(
                    Deduction(
                        category=Category.TESTS,
                        points=points,
                        reason="SonarQube line and branch coverage leave executable code untested",
                        metric="sonarqube.coverage",
                    )
                )

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


def _float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
