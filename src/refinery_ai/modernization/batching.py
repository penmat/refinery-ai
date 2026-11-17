"""Finite ready-objective batches, all transformations pinned in the destination."""

from uuid import uuid4

from refinery_ai.config import Settings
from refinery_ai.context_graph.models import ContextGraphInfo
from refinery_ai.migrations.execution_state import StepReceipt
from refinery_ai.models import (
    RecipePlan,
    RecipePriority,
    RecipeRecommendation,
    RunReport,
    RunStatus,
)
from refinery_ai.modernization.evidence import ProjectEvidence
from refinery_ai.modernization.models import ModernizationIntentPlan
from refinery_ai.modernization.progress import ModernizationProgress
from refinery_ai.modernization.store import intent_digest
from refinery_ai.modernization.transformations import execution_identity
from refinery_ai.modernization.validation import validate_batch
from refinery_ai.planning_artifacts import MigrationPlanStore


def plan_batch(
    intent: ModernizationIntentPlan,
    progress: ModernizationProgress,
    evidence: ProjectEvidence,
    settings: Settings,
    receipts: list[StepReceipt] | None = None,
) -> RunReport:
    statuses = {o.objective_id: o.status for o in progress.objectives}
    steps: list[RecipeRecommendation] = []
    completed = {
        execution_identity(r.recipe)
        for r in (receipts or [])
        if r.execution.green and r.verification.green
    }
    for objective in intent.objectives:
        if (
            statuses.get(objective.id) != "pending"
            or objective.disposition != "executable"
            or any(statuses.get(parent) != "satisfied" for parent in objective.depends_on)
        ):
            continue
        for transformation in objective.transformations:
            step = RecipeRecommendation(
                recipe=transformation.recipe,
                recipe_artifact=transformation.artifact,
                options=transformation.options,
                objective_id=objective.id,
                reason=objective.rationale,
                evidence=objective.evidence_ids,
                expected_benefit=objective.rationale,
                priority=RecipePriority.HIGH,
                confidence=1,
            )
            if objective.category != "source" or execution_identity(step) not in completed:
                if len(steps) == 13:
                    break
                steps.append(step)
    batch = RecipePlan(recommendations=steps)
    validate_batch(batch, intent)
    report = RunReport(
        run_id=uuid4().hex,
        status=RunStatus.PLANNED,
        snapshot=evidence.snapshot,
        plan=batch,
        context_graph=ContextGraphInfo(
            graph_path=intent.repository / settings.state_directory / "context/context-graph.json",
            provider="destination-plan",
        ),
        modernization_plan_id=intent.plan_id,
        modernization_plan_digest=intent_digest(intent),
    )
    MigrationPlanStore(intent.repository, settings.state_directory).save(report)
    return report
