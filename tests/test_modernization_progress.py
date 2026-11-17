from test_modernization_models import intent

from refinery_ai.models import BuildSystem, DiscoverySnapshot
from refinery_ai.modernization.evidence import ProjectEvidence
from refinery_ai.modernization.progress import check_objectives


def evidence(root, java="21"):
    return ProjectEvidence(
        snapshot=DiscoverySnapshot(
            repository=root, build_system=BuildSystem.MAVEN, java_version=java
        ),
        revision="abc",
        build_fingerprint="build",
        source_fingerprint="source",
        module_versions={".": {"java": java, "spring_boot": None}},
    )


def test_exact_versions_all_modules(tmp_path):
    plan = intent(tmp_path)
    result = evidence(tmp_path)
    assert check_objectives(plan, result, [])[0].status == "satisfied"
    result.module_versions["child"] = {"java": "17"}
    assert check_objectives(plan, result, [])[0].status == "pending"


def test_source_requires_verified_receipt(tmp_path):
    from refinery_ai.modernization.models import CompletionPredicate

    plan = intent(tmp_path)
    plan.objectives[0].completion = CompletionPredicate(kind="receipt")
    assert check_objectives(plan, evidence(tmp_path), [])[0].status == "pending"


def test_security_scan_outage_leaves_objective_incomplete(tmp_path):
    from refinery_ai.modernization.models import CompletionPredicate

    plan = intent(tmp_path)
    plan.objectives[0].completion = CompletionPredicate(
        kind="finding", analyzer="dependencies", finding_ids=["CVE-test"]
    )
    assert check_objectives(plan, evidence(tmp_path), [])[0].status == "blocked"


def test_source_finding_disappearance_still_requires_receipts(tmp_path):
    from refinery_ai.modernization.models import AnalysisAvailability, CompletionPredicate

    plan = intent(tmp_path)
    plan.objectives[0].category = "source"
    plan.objectives[0].completion = CompletionPredicate(
        kind="finding", analyzer="sonarqube", finding_ids=["rule"]
    )
    observed = evidence(tmp_path)
    observed.analysis = [AnalysisAvailability(analyzer="sonarqube", status="completed")]
    observed.findings = {"sonarqube": []}
    assert check_objectives(plan, observed, [])[0].status == "pending"


def test_bom_security_target_checks_owner_and_fresh_graph(tmp_path):
    from refinery_ai.modernization.models import (
        AnalysisAvailability,
        CompletionPredicate,
        TargetSelection,
    )

    plan = intent(tmp_path)
    target = TargetSelection(component="dependency", group="g", artifact="bom", version="2.0")
    plan.targets = [target]
    objective = plan.objectives[0]
    objective.target = target
    objective.category = "security"
    objective.completion = CompletionPredicate(
        kind="finding", analyzer="dependencies", finding_ids=["CVE-test"]
    )
    observed = evidence(tmp_path)
    observed.analysis = [AnalysisAvailability(analyzer="dependencies", status="completed")]
    observed.owners = {"g:bom": {"kind": "bom", "version": "2.0"}}
    observed.findings = {"dependencies": []}
    assert check_objectives(plan, observed, [])[0].status == "satisfied"
    observed.owners["g:bom"]["version"] = "1.0"
    assert check_objectives(plan, observed, [])[0].status == "pending"
