from test_modernization_models import intent
from test_modernization_progress import evidence

from refinery_ai.config import Settings
from refinery_ai.modernization.batching import plan_batch
from refinery_ai.modernization.models import TransformationSpec
from refinery_ai.modernization.progress import ModernizationProgress, ObjectiveProgress


def test_large_objective_is_not_truncated_or_starved(tmp_path):
    plan = intent(tmp_path)
    plan.objectives[0].transformations = [
        TransformationSpec(recipe=f"example.Step{i}", artifact="g:a:1") for i in range(15)
    ]
    progress = ModernizationProgress(
        plan_id=plan.plan_id,
        plan_digest="digest",
        expected_revision="abc",
        build_fingerprint="build",
        source_fingerprint="source",
        settings_digest="settings",
        objectives=[ObjectiveProgress(objective_id="java")],
    )
    batch = plan_batch(plan, progress, evidence(tmp_path), Settings())
    assert len(batch.plan.recommendations) == 13
    assert batch.plan.recommendations[-1].recipe == "example.Step12"


def test_java_finalizer_can_run_again_after_framework_changes(tmp_path):
    from types import SimpleNamespace

    plan = intent(tmp_path)
    progress = ModernizationProgress(
        plan_id=plan.plan_id,
        plan_digest="digest",
        expected_revision="abc",
        build_fingerprint="build",
        source_fingerprint="source",
        settings_digest="settings",
        objectives=[ObjectiveProgress(objective_id="java")],
    )
    first = plan_batch(plan, progress, evidence(tmp_path, java="17"), Settings())
    receipt = SimpleNamespace(
        recipe=first.plan.recommendations[0],
        execution=SimpleNamespace(green=True),
        verification=SimpleNamespace(green=True),
    )
    later = plan_batch(plan, progress, evidence(tmp_path, java="17"), Settings(), [receipt])
    assert len(later.plan.recommendations) == 1
