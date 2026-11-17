from pathlib import Path

import pytest
from pydantic import ValidationError

from refinery_ai.modernization.models import (
    CompletionPredicate,
    EvidenceReference,
    ModernizationIntentPlan,
    ModernizationObjective,
    TargetSelection,
    TransformationSpec,
)
from refinery_ai.modernization.policy import mode_policy
from refinery_ai.modernization.store import IntentPlanStore, intent_digest


def intent(root: Path, **changes):
    values = dict(
        repository=root,
        source_revision="abc",
        build_fingerprint="build",
        source_fingerprint="source",
        requested_jdk=21,
        evidence=[EvidenceReference(id="discovery", source="pom.xml", detail="Java 8")],
        targets=[TargetSelection(component="java", version="21", evidence_ids=["discovery"])],
        objectives=[
            ModernizationObjective(
                id="java",
                category="compatibility",
                rationale="Requested JDK",
                evidence_ids=["discovery"],
                target=TargetSelection(component="java", version="21", evidence_ids=["discovery"]),
                transformations=[TransformationSpec(recipe="example.Java21", artifact="g:a:1.0")],
                completion=CompletionPredicate(kind="version", component="java", version="21"),
            )
        ],
    )
    values.update(changes)
    return ModernizationIntentPlan(**values)


def test_mode_policy_matrix():
    assert not mode_policy("minimal").dependencies
    assert mode_policy("recommended").security
    assert mode_policy("recommended").dependencies
    assert not mode_policy("recommended").sonarqube
    assert not mode_policy("recommended").codegraph
    assert mode_policy("full").sonarqube and mode_policy("full").codegraph


def test_intent_rejects_invalid_graph(tmp_path):
    plan = intent(tmp_path)
    objective = plan.objectives[0]
    for objectives in (
        [objective, objective],
        [objective.model_copy(update={"depends_on": ["missing"]})],
        [objective.model_copy(update={"evidence_ids": ["missing"]})],
        [objective.model_copy(update={"depends_on": ["java"]})],
    ):
        with pytest.raises(ValidationError):
            intent(tmp_path, objectives=objectives)
    with pytest.raises(ValidationError):
        TargetSelection(component="java", version="latest")
    with pytest.raises(ValidationError):
        intent(tmp_path, schema_version=2)


def test_store_roundtrip_and_immutability(tmp_path):
    plan = intent(tmp_path)
    store = IntentPlanStore(tmp_path)
    path = store.save(plan)
    assert intent_digest(store.load(plan.plan_id)) == intent_digest(plan)
    assert "Requested JDK" in path.with_suffix(".md").read_text()
    assert store.save(plan) == path
    with pytest.raises(ValueError, match="immutable"):
        store.save(plan.model_copy(update={"limitations": ["changed"]}))
    for bad in ("../escape", "/tmp/file", "x/y"):
        with pytest.raises(ValueError):
            store.load(bad)


def test_store_detects_external_plan_edit(tmp_path):
    plan = intent(tmp_path)
    store = IntentPlanStore(tmp_path)
    path = store.save(plan)
    path.write_text(path.read_text().replace("Requested JDK", "Tampered JDK"))
    with pytest.raises(ValueError, match="digest"):
        store.load(plan.plan_id)


def test_completion_cannot_disagree_with_destination(tmp_path):
    plan = intent(tmp_path)
    objective = plan.objectives[0]
    for modified in (
        objective.model_copy(update={"target": None}),
        objective.model_copy(
            update={
                "completion": CompletionPredicate(kind="version", component="java", version="17")
            }
        ),
        objective.model_copy(update={"target": TargetSelection(component="java", version="25")}),
    ):
        with pytest.raises(ValidationError):
            intent(tmp_path, objectives=[modified])


def test_intent_summary_rejects_symlink_temp_file(tmp_path):
    plan = intent(tmp_path)
    store = IntentPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve")
    (store.root / f"{plan.plan_id}.md.tmp").symlink_to(outside)
    with pytest.raises(ValueError, match=r"symlink|escapes"):
        store.save(plan)
    assert outside.read_text() == "preserve"
