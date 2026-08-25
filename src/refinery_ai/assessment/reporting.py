"""Concise terminal reports for assessments and comparisons."""

from __future__ import annotations

from refinery_ai.assessment.models import Assessment, AssessmentComparison, Category

_LABELS = {
    Category.PLATFORM: "Platform Modernization",
    Category.DEPENDENCIES: "Dependency Health",
    Category.MAINTAINABILITY: "Maintainability",
    Category.RISK: "Defect & Security Risk",
    Category.ARCHITECTURE: "Architecture Health",
    Category.TESTS: "Test Confidence",
    Category.BUILD: "Build & Delivery Health",
}


def assessment_text(assessment: Assessment) -> str:
    overall = (
        "Not assessed" if assessment.score.overall is None else f"{assessment.score.overall:g}/100"
    )
    lines = [
        f"Modernization Score: {overall}",
        f"Evidence completeness: {assessment.completeness:.0%}",
        "",
    ]
    for category in Category:
        value = assessment.score.categories.get(category)
        rendered = "not assessed" if value is None else f"{value:g}"
        lines.append(f"{_LABELS[category]:28} {rendered}")
    lines.extend(["", f"Assessment ID: {assessment.assessment_id}"])
    if assessment.name:
        lines.append(f"Name: {assessment.name}")
    sonar = assessment.analyzers.get("sonarqube")
    if sonar is not None:
        lines.extend(["", f"SonarQube: {sonar.status.value}"])
        lines.extend(f"  {item.message}" for item in sonar.diagnostics)
    dependencies = assessment.analyzers.get("dependencies")
    if dependencies is not None:
        lines.extend(["", f"Dependency analysis: {dependencies.status.value}"])
        lines.extend(f"  {item.message}" for item in dependencies.diagnostics)
    return "\n".join(lines)


def comparison_text(previous: Assessment, current: Assessment, delta: AssessmentComparison) -> str:
    change = "suppressed" if delta.overall_change is None else f"{delta.overall_change:+g}"
    lines = [
        "Modernization Score",
        "",
        f"Baseline: {_score(previous)}",
        f"Current:  {_score(current)}",
        f"Change:   {change}",
        f"Compatibility: {delta.compatibility.value}",
    ]
    if delta.reason:
        lines.append(delta.reason)
    category_rows = [
        (category, value) for category, value in delta.category_changes.items() if value is not None
    ]
    if category_rows:
        lines.extend(["", "Category changes:"])
        lines.extend(f"  {_LABELS[category]:26} {value:+g}" for category, value in category_rows)
    if delta.metric_changes:
        lines.extend(["", "Metric changes:"])
        lines.extend(
            f"  {item.metric}: {item.before:g} -> {item.after:g} ({item.change:+g})"
            for item in delta.metric_changes
        )
    lines.extend(
        [
            "",
            f"Resolved findings: {len(delta.resolved_findings)}",
            f"New findings:      {len(delta.introduced_findings)}",
        ]
    )
    return "\n".join(lines)


def _score(assessment: Assessment) -> str:
    return "not assessed" if assessment.score.overall is None else f"{assessment.score.overall:g}"
