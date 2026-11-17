"""Bounded mode workflow with a durable journal at each child migration boundary."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from refinery_ai.models import RecipePlan, RunStatus
from refinery_ai.modernization.batching import plan_batch
from refinery_ai.modernization.evidence import ProjectEvidence
from refinery_ai.modernization.models import ModernizationIntentPlan
from refinery_ai.modernization.progress import ModernizationProgress, check_objectives
from refinery_ai.modernization.runner import (
    ModernizationRunner,
    ModernizationRunResult,
    ModernizationRunStore,
)
from refinery_ai.modernization.transformations import execution_identity


class ModeGraphState(TypedDict, total=False):
    child: str
    result: ModernizationRunResult


def run_modernization_graph(
    runner: ModernizationRunner,
    intent: ModernizationIntentPlan,
    progress: ModernizationProgress,
    store: ModernizationRunStore,
    max_cycles: int,
) -> ModernizationRunResult:
    from refinery_ai.modernization.runner import ModernizationRunResult

    fresh_limitations: list[str] = []

    def record_coverage(evidence: ProjectEvidence) -> None:
        fresh_limitations[:] = list(evidence.limitations)
        for purl, findings in evidence.vulnerabilities.items():
            for finding in findings:
                if not finding.get("withdrawn"):
                    fresh_limitations.append(
                        f"Unresolved advisory {finding['id']} in {purl}; regenerate a plan "
                        f"to address newly detected findings"
                    )

    def finish(message: str, status: str = "needs_review") -> ModernizationRunResult:
        return ModernizationRunResult(
            plan_id=intent.plan_id,
            status=status,
            cycles=progress.cycles,
            message=message,
            limitations=list(dict.fromkeys([*intent.limitations, *fresh_limitations])),
            objectives=[
                {"id": p.objective_id, "status": p.status, "reason": p.reason}
                for p in progress.objectives
            ],
        )

    def analyze(state: ModeGraphState) -> ModeGraphState:
        evidence = runner.collect(intent)
        record_coverage(evidence)
        expected = (
            progress.expected_revision,
            progress.build_fingerprint,
            progress.source_fingerprint,
        )
        observed = (evidence.revision, evidence.build_fingerprint, evidence.source_fingerprint)
        if expected != observed:
            if runner.may_reconcile_publication(progress, evidence):
                # Child recovery validates the entire receipt/ref chain and clean checkout.
                assert progress.pending_child is not None
                return {"child": progress.pending_child}
            if not runner.accept_owned_publication(progress, evidence):
                raise ValueError("Repository inputs changed outside this run; regenerate plan")
            progress.expected_revision, progress.build_fingerprint, progress.source_fingerprint = (
                observed
            )
        if progress.pending_child is not None:
            # The child owns recovery, verified receipts and publication reconciliation.
            return {"child": progress.pending_child}
        progress.objectives = check_objectives(intent, evidence, runner.receipts(progress))
        store.save(progress)
        if all(p.status in {"satisfied", "advisory"} for p in progress.objectives):
            if not runner.verify_final(intent):
                return {"result": finish("Final build/tests did not pass")}
            final = runner.collect(intent)
            record_coverage(final)
            if (final.revision, final.build_fingerprint, final.source_fingerprint) != observed:
                raise ValueError("Inputs changed during final verification; regenerate plan")
            progress.objectives = check_objectives(intent, final, runner.receipts(progress))
            store.save(progress)
            if not all(p.status in {"satisfied", "advisory"} for p in progress.objectives):
                return {"result": finish("Final objective verification is incomplete")}
            return {
                "result": finish(
                    "All executable objectives and final build/tests verified", "completed"
                )
            }
        if progress.cycles >= max_cycles:
            return {"result": finish("Maximum modernization cycle count reached")}
        report = plan_batch(intent, progress, evidence, runner.settings, runner.receipts(progress))
        assert isinstance(report.plan, RecipePlan)
        if not report.plan.recommendations:
            return {"result": finish("No executable batch while objectives remain unsatisfied")}
        identity = [execution_identity(s) for s in report.plan.recommendations]
        if identity == progress.previous_batch:
            return {"result": finish("Repeated batch without measurable objective progress")}
        progress.child_ids.append(report.run_id)
        progress.cycles += 1
        progress.pending_child = report.run_id
        progress.previous_batch = identity
        store.save(progress)
        return {"child": report.run_id}

    def migrate(state: ModeGraphState) -> ModeGraphState:
        result = runner.migrate(state["child"])
        if result.status != RunStatus.COMPLETED:
            return {"result": finish("Migration failed or requires review")}
        evidence = runner.collect(intent)
        record_coverage(evidence)
        if result.published_revision:
            if (
                result.published_revision != evidence.revision
                or not runner.accept_owned_publication(progress, evidence)
            ):
                raise ValueError("Unexpected inputs after migration; regenerate plan")
            progress.expected_revision = evidence.revision
            progress.build_fingerprint = evidence.build_fingerprint
            progress.source_fingerprint = evidence.source_fingerprint
        progress.pending_child = None
        store.save(progress)
        return {}

    graph = StateGraph(ModeGraphState)
    graph.add_node("analyze", analyze)
    graph.add_node("migrate", migrate)
    graph.add_edge(START, "analyze")
    graph.add_conditional_edges("analyze", lambda s: END if "result" in s else "migrate")
    graph.add_conditional_edges("migrate", lambda s: END if "result" in s else "analyze")
    result = graph.compile().invoke({}, config={"recursion_limit": max_cycles * 3 + 10})
    return ModernizationRunResult.model_validate(result["result"])
