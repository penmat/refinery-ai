"""Mutable objective progress, never part of the saved destination digest."""

from typing import Literal

from pydantic import Field

from refinery_ai.migrations.execution_state import StepReceipt
from refinery_ai.modernization.evidence import ProjectEvidence
from refinery_ai.modernization.models import Contract, ModernizationIntentPlan


class ObjectiveProgress(Contract):
    objective_id: str
    status: Literal["pending", "satisfied", "blocked", "advisory"] = "pending"
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)


class ModernizationProgress(Contract):
    plan_id: str
    plan_digest: str
    expected_revision: str
    build_fingerprint: str
    source_fingerprint: str
    settings_digest: str
    cycles: int = 0
    pending_child: str | None = None
    previous_batch: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    objectives: list[ObjectiveProgress] = Field(default_factory=list)


def check_objectives(
    intent: ModernizationIntentPlan, evidence: ProjectEvidence, receipts: list[StepReceipt]
) -> list[ObjectiveProgress]:
    """Recheck versions/findings; only verified, parent-bound receipts prove source work."""
    from refinery_ai.modernization.resolver import java_major

    availability = {a.analyzer: a.status for a in evidence.analysis}
    result = []
    for objective in intent.objectives:
        status: Literal["pending", "satisfied", "blocked", "advisory"] = "pending"
        reason = "Completion predicate is not satisfied"
        predicate = objective.completion
        if objective.disposition == "advisory":
            result.append(
                ObjectiveProgress(
                    objective_id=objective.id, status="advisory", reason=objective.rationale
                )
            )
            continue
        target = objective.target
        target_ok = True
        if target:
            if target.component in {"java", "spring_boot"}:
                values = [v.get(target.component) for v in evidence.module_versions.values()]
                if target.component == "spring_boot":
                    values = [v for v in values if v is not None]
                target_ok = bool(values) and all(
                    str(java_major(v)) == target.version
                    if target.component == "java"
                    else v == target.version
                    for v in values
                )
            elif target.component == "dependency":
                values = [
                    d["version"]
                    for d in evidence.inventory
                    if d["group"] == target.group and d["artifact"] == target.artifact
                ]
                owner = evidence.owners.get(f"{target.group}:{target.artifact}", {})
                if not values and owner.get("kind") in {"bom", "parent"}:
                    values = [owner.get("version")]
                target_ok = bool(values) and all(v == target.version for v in values)
        verified = [
            r
            for r in receipts
            if r.recipe.objective_id == objective.id and r.verification.green and r.execution.green
        ]
        identities = {
            (r.recipe.recipe, r.recipe.recipe_artifact, tuple(sorted(r.recipe.options.items())))
            for r in verified
        }
        expected = {
            (t.recipe, t.artifact, tuple(sorted(t.options.items())))
            for t in objective.transformations
        }
        if predicate.kind == "version" and target_ok:
            status, reason = "satisfied", "Exact target verified in discovered modules"
        elif predicate.kind == "finding":
            if availability.get(predicate.analyzer or "") != "completed":
                status, reason = "blocked", "Fresh complete analyzer evidence is unavailable"
            else:
                findings = set(evidence.findings.get(predicate.analyzer or "", []))
                if (
                    not findings.intersection(predicate.finding_ids)
                    and target_ok
                    and (
                        objective.category != "source"
                        or (bool(expected) and expected <= identities)
                    )
                ):
                    status, reason = "satisfied", "Target reached and targeted findings absent"
        elif (
            predicate.kind == "receipt"
            and expected
            and expected <= identities
            and predicate.no_change_proves_satisfied
        ):
            status, reason = "satisfied", "Verified transformation receipts prove predicate"
        result.append(ObjectiveProgress(objective_id=objective.id, status=status, reason=reason))
    return result
