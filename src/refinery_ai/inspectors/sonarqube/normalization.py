"""Normalize SonarQube measures and issues into inspector evidence."""

from __future__ import annotations

import hashlib
from typing import Any

from refinery_ai.assessment.models import Category, Finding

METRIC_KEYS = [
    "ncloc",
    "coverage",
    "line_coverage",
    "branch_coverage",
    "lines_to_cover",
    "uncovered_lines",
    "conditions_to_cover",
    "uncovered_conditions",
    "complexity",
    "cognitive_complexity",
    "duplicated_lines_density",
    "duplicated_blocks",
    "reliability_rating",
    "security_rating",
    "security_review_rating",
    "sqale_rating",
    "sqale_index",
    "software_quality_blocker_issues",
    "software_quality_high_issues",
    "software_quality_medium_issues",
    "alert_status",
]

_NAMES = {
    "line_coverage": "lineCoverage",
    "branch_coverage": "branchCoverage",
    "lines_to_cover": "linesToCover",
    "uncovered_lines": "uncoveredLines",
    "conditions_to_cover": "conditionsToCover",
    "uncovered_conditions": "uncoveredConditions",
    "cognitive_complexity": "cognitiveComplexity",
    "duplicated_lines_density": "duplicatedLinesDensity",
    "duplicated_blocks": "duplicatedBlocks",
    "reliability_rating": "reliabilityRating",
    "security_rating": "securityRating",
    "security_review_rating": "securityReviewRating",
    "sqale_rating": "maintainabilityRating",
    "sqale_index": "technicalDebtMinutes",
    "software_quality_blocker_issues": "blockerIssues",
    "software_quality_high_issues": "highIssues",
    "software_quality_medium_issues": "mediumIssues",
    "alert_status": "qualityGateStatus",
}


def normalize_metrics(raw: dict[str, Any]) -> dict[str, int | float | str]:
    result: dict[str, int | float | str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        normalized = _number(value)
        result[_NAMES.get(key, key)] = normalized
    return result


def normalize_findings(issues: list[dict[str, Any]], project_key: str) -> list[Finding]:
    findings: list[Finding] = []
    for issue in issues:
        status = str(issue.get("status", "OPEN"))
        if status in {"CLOSED", "RESOLVED", "ACCEPTED", "FALSE-POSITIVE"}:
            continue
        quality, severity = _quality_and_severity(issue)
        category = Category.MAINTAINABILITY if quality == "MAINTAINABILITY" else Category.RISK
        component = str(issue.get("component", ""))
        path = component.removeprefix(f"{project_key}:") or None
        rule = str(issue.get("rule", "unknown"))
        message = str(issue.get("message", "SonarQube issue"))
        line = issue.get("line")
        fingerprint = _fingerprint(rule, path, line, message)
        findings.append(
            Finding(
                fingerprint=f"sonarqube:{fingerprint}",
                analyzer="sonarqube",
                rule=rule,
                category=category,
                severity=severity.lower(),
                message=message,
                path=path,
                line=int(line) if isinstance(line, int | str) and str(line).isdigit() else None,
            )
        )
    return findings


def _quality_and_severity(issue: dict[str, Any]) -> tuple[str, str]:
    impacts = issue.get("impacts", [])
    if isinstance(impacts, list) and impacts and isinstance(impacts[0], dict):
        quality = str(impacts[0].get("softwareQuality", "MAINTAINABILITY"))
        severity = str(impacts[0].get("severity", issue.get("severity", "MEDIUM")))
        return quality, severity
    issue_type = str(issue.get("type", "CODE_SMELL"))
    quality = {
        "BUG": "RELIABILITY",
        "VULNERABILITY": "SECURITY",
        "SECURITY_HOTSPOT": "SECURITY",
    }.get(issue_type, "MAINTAINABILITY")
    return quality, str(issue.get("severity", "MEDIUM"))


def _fingerprint(rule: str, path: str | None, line: object, message: str) -> str:
    value = f"sonarqube|{rule}|{path or ''}|{line or ''}|{' '.join(message.split())}"
    return hashlib.sha256(value.encode()).hexdigest()


def _number(value: Any) -> int | float | str:
    if not isinstance(value, str):
        return value if isinstance(value, int | float) else str(value)
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number
