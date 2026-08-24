"""Compatible assessment comparison and stable finding matching."""

from __future__ import annotations

from typing import Any

from refinery_ai.assessment.models import (
    Assessment,
    AssessmentComparison,
    Category,
    ComparisonCompatibility,
    MetricDelta,
)


def compare(previous: Assessment, current: Assessment) -> AssessmentComparison:
    same_profile = previous.profile.fingerprint == current.profile.fingerprint
    compatibility = (
        ComparisonCompatibility.COMPATIBLE
        if same_profile
        else ComparisonCompatibility.PARTIALLY_COMPATIBLE
    )
    previous_findings = {finding.fingerprint: finding for finding in previous.findings}
    current_findings = {finding.fingerprint: finding for finding in current.findings}
    category_changes: dict[Category, float | None] = {}
    for category in Category:
        before = previous.score.categories.get(category)
        after = current.score.categories.get(category)
        category_changes[category] = (
            round(after - before, 1)
            if same_profile and before is not None and after is not None
            else None
        )
    overall_change = None
    if same_profile and previous.score.overall is not None and current.score.overall is not None:
        overall_change = round(current.score.overall - previous.score.overall, 1)
    return AssessmentComparison(
        previous_id=previous.assessment_id,
        current_id=current.assessment_id,
        compatibility=compatibility,
        reason=None
        if same_profile
        else "Scoring profile fingerprints differ; score deltas are suppressed",
        overall_change=overall_change,
        category_changes=category_changes,
        metric_changes=_metric_deltas(previous.metrics, current.metrics),
        resolved_findings=[
            previous_findings[key]
            for key in sorted(previous_findings.keys() - current_findings.keys())
        ],
        introduced_findings=[
            current_findings[key]
            for key in sorted(current_findings.keys() - previous_findings.keys())
        ],
    )


def _metric_deltas(previous: dict[str, Any], current: dict[str, Any]) -> list[MetricDelta]:
    before = _flatten(previous)
    after = _flatten(current)
    deltas = []
    for key in sorted(before.keys() & after.keys()):
        left, right = before[key], after[key]
        if isinstance(left, bool) or isinstance(right, bool):
            continue
        if isinstance(left, (int, float)) and isinstance(right, (int, float)) and left != right:
            deltas.append(
                MetricDelta(metric=key, before=left, after=right, change=round(right - left, 3))
            )
    return deltas


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(_flatten(item, path))
        else:
            result[path] = item
    return result
