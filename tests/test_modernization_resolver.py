from refinery_ai.config import Settings
from refinery_ai.models import BuildSystem, DiscoverySnapshot
from refinery_ai.modernization.evidence import ProjectEvidence
from refinery_ai.modernization.models import EvidenceReference
from refinery_ai.modernization.resolver import DestinationChoices, resolve_intent
from refinery_ai.recipes.catalog import RecipeCatalogSession
from refinery_ai.recipes.sources import RecipeSource


def evidence(root):
    return ProjectEvidence(
        snapshot=DiscoverySnapshot(
            repository=root, build_system=BuildSystem.MAVEN, java_version="17"
        ),
        revision="abc",
        build_fingerprint="b",
        source_fingerprint="s",
        references=[EvidenceReference(id="discovery", source="pom.xml", detail="Java 17")],
        module_versions={".": {"java": "17", "spring_boot": None}},
    )


class Planner:
    def resolve(self, request):
        return DestinationChoices(rationale="Meet requested Java target")


def test_destination_saved_before_execution(tmp_path):
    source = RecipeSource(
        recipe="org.openrewrite.java.migrate.UpgradeBuildToJava21",
        display_name="Java 21",
        description="Upgrade",
        repository="openrewrite/rewrite-migrate-java",
        file_path="java21.yml",
        recipe_list=[],
        artifact="org.openrewrite.recipe:rewrite-migrate-java:3.42.1",
    )
    result = resolve_intent(
        tmp_path,
        21,
        "recommended",
        Settings(),
        planner=Planner(),
        evidence=evidence(tmp_path),
        catalog=RecipeCatalogSession([source]),
    )
    assert result.targets[0].version == "21"
    assert result.objectives[0].transformations
    assert (tmp_path / ".refinery/modernization-plans" / f"{result.plan_id}.json").exists()


def test_lower_java_rejected(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="downgrade"):
        resolve_intent(
            tmp_path,
            11,
            "minimal",
            Settings(),
            planner=Planner(),
            evidence=evidence(tmp_path),
            catalog=RecipeCatalogSession([]),
        )


def test_unsupported_recipe_blocks_required_target(tmp_path):
    result = resolve_intent(
        tmp_path,
        21,
        "minimal",
        Settings(),
        planner=Planner(),
        evidence=evidence(tmp_path),
        catalog=RecipeCatalogSession([]),
    )
    assert result.blocked_reasons


def test_non_spring_does_not_gain_framework(tmp_path):
    class BadPlanner:
        def resolve(self, request):
            return DestinationChoices(spring_boot_version="3.5.16", rationale="invented")

    result = resolve_intent(
        tmp_path,
        17,
        "recommended",
        Settings(),
        planner=BadPlanner(),
        evidence=evidence(tmp_path),
        catalog=RecipeCatalogSession([]),
    )
    assert any("Spring" in reason for reason in result.blocked_reasons)
    assert all(t.component != "spring_boot" for t in result.targets)


def test_java_destination_does_not_execute_broad_unpinned_upgrade(tmp_path):
    catalog = RecipeCatalogSession(
        [
            RecipeSource(
                recipe="org.openrewrite.java.migrate.UpgradeBuildToJava21",
                display_name="Java 21 build",
                description="Java version only",
                repository="openrewrite/rewrite-migrate-java",
                file_path="java21.yml",
                recipe_list=[{"org.openrewrite.java.migrate.UpgradeJavaVersion": {"version": 21}}],
                artifact="org.openrewrite.recipe:rewrite-migrate-java:3.42.1",
            )
        ]
    )
    plan = resolve_intent(
        tmp_path,
        21,
        "recommended",
        Settings(),
        planner=Planner(),
        evidence=evidence(tmp_path),
        catalog=catalog,
    )
    assert not plan.blocked_reasons
    assert plan.objectives[0].transformations[0].recipe.endswith("UpgradeBuildToJava21")


def test_recommended_selects_security_checked_maintenance_patch(tmp_path, monkeypatch):
    from refinery_ai.inspectors.dependencies.osv import OsvClient
    from refinery_ai.models import Dependency
    from refinery_ai.modernization.versions import VersionCandidate, VersionCandidateProvider

    observed = evidence(tmp_path)
    observed.snapshot.dependencies = [Dependency(group="g", artifact="lib", version="1.0.0")]
    observed.inventory = [{"group": "g", "artifact": "lib", "version": "1.0.0", "direct": True}]
    monkeypatch.setattr(
        VersionCandidateProvider,
        "candidates",
        lambda *args: [
            VersionCandidate(
                version=v, source="https://repo.example/metadata", retrieved_at="2026-11-15"
            )
            for v in ["1.0.1", "1.1.0", "2.0.0"]
        ],
    )
    monkeypatch.setattr(OsvClient, "query", lambda self, purls: {p: [] for p in purls})
    catalog = RecipeCatalogSession(
        [
            RecipeSource(
                recipe="org.openrewrite.maven.UpgradeDependencyVersion",
                display_name="Upgrade",
                description="Upgrade",
                repository="openrewrite/rewrite-maven",
                file_path="descriptor",
                artifact="org.openrewrite:rewrite-maven:8.90.2",
            )
        ]
    )
    plan = resolve_intent(
        tmp_path,
        17,
        "recommended",
        Settings(),
        planner=Planner(),
        evidence=observed,
        catalog=catalog,
    )
    upgrades = [o for o in plan.objectives if o.category == "dependency"]
    assert len(upgrades) == 1
    assert upgrades[0].target.version == "1.0.1"
    assert upgrades[0].transformations[0].options["newVersion"] == "1.0.1"


def test_model_cannot_label_dependency_upgrade_as_source(tmp_path):
    from refinery_ai.modernization.models import (
        CompletionPredicate,
        ModernizationObjective,
        TransformationSpec,
    )

    class MislabelledPlanner:
        def resolve(self, request):
            return DestinationChoices(
                rationale="Upgrade",
                objectives=[
                    ModernizationObjective(
                        id="sneaky",
                        category="source",
                        rationale="Cleanup",
                        evidence_ids=["discovery"],
                        transformations=[
                            TransformationSpec(
                                recipe="example.ChangeFramework",
                                artifact="g:a:1",
                                effects=["source"],
                            )
                        ],
                        completion=CompletionPredicate(
                            kind="receipt", no_change_proves_satisfied=True
                        ),
                    )
                ],
            )

    catalog = RecipeCatalogSession(
        [
            RecipeSource(
                recipe="example.ChangeFramework",
                display_name="Change framework",
                description="Changes framework",
                file_path="test.yml",
                repository="example/recipes",
                artifact="g:a:1",
            )
        ]
    )
    result = resolve_intent(
        tmp_path,
        17,
        "recommended",
        Settings(),
        planner=MislabelledPlanner(),
        evidence=evidence(tmp_path),
        catalog=catalog,
    )
    objective = next(o for o in result.objectives if o.id == "sneaky")
    assert objective.disposition == "advisory"
    assert not objective.transformations


def test_major_framework_destination_has_atomic_exact_transformation(tmp_path):
    from refinery_ai.recipes.sources import RecipeSource

    observed = evidence(tmp_path)
    observed.snapshot.spring_boot_version = "2.7.18"
    observed.module_versions["."]["spring_boot"] = "2.7.18"
    observed.owners = {
        "org.springframework.boot:spring-boot-starter-parent": {
            "version": "2.7.18",
            "kind": "parent",
        }
    }

    class MajorPlanner:
        def resolve(self, request):
            return DestinationChoices(
                spring_boot_version="3.5.16", rationale="Use the verified Java 17 baseline"
            )

    catalog = RecipeCatalogSession(
        [
            RecipeSource(
                recipe="refinery.UpgradeSpringBoot",
                display_name="Pinned Boot",
                description="Atomic owner and namespace migration",
                repository="refinery",
                file_path="modernization/transformations.py",
                artifact="org.openrewrite.recipe:rewrite-migrate-java:3.42.1",
            )
        ]
    )
    # No vulnerabilities means no BOM candidate network fetch is necessary.
    result = resolve_intent(
        tmp_path,
        17,
        "recommended",
        Settings(),
        planner=MajorPlanner(),
        evidence=observed,
        catalog=catalog,
    )
    assert not result.blocked_reasons
    step = next(o for o in result.objectives if o.id == "spring-boot").transformations[0]
    assert step.options["newVersion"] == "3.5.16"
    assert step.options["migrateJakarta"] is True


def test_source_finding_predicate_is_preserved(tmp_path):
    from refinery_ai.modernization.models import (
        AnalysisAvailability,
        CompletionPredicate,
        ModernizationObjective,
        TransformationSpec,
    )

    observed = evidence(tmp_path)
    observed.analysis = [AnalysisAvailability(analyzer="sonarqube", status="completed")]
    observed.findings = {"sonarqube": ["java:S6126"]}

    class SourcePlanner:
        def resolve(self, request):
            return DestinationChoices(
                rationale="Readable strings",
                objectives=[
                    ModernizationObjective(
                        id="text-blocks",
                        category="source",
                        rationale="Address string finding",
                        evidence_ids=["discovery"],
                        transformations=[
                            TransformationSpec(
                                recipe="org.openrewrite.java.migrate.lang.UseTextBlocks",
                                artifact="g:a:1",
                            )
                        ],
                        completion=CompletionPredicate(
                            kind="finding", analyzer="sonarqube", finding_ids=["java:S6126"]
                        ),
                    )
                ],
            )

    catalog = RecipeCatalogSession(
        [
            RecipeSource(
                recipe="org.openrewrite.java.migrate.lang.UseTextBlocks",
                display_name="Text blocks",
                description="Source",
                repository="test",
                file_path="source.yml",
                artifact="g:a:1",
            )
        ]
    )
    plan = resolve_intent(
        tmp_path,
        17,
        "full",
        Settings(),
        planner=SourcePlanner(),
        evidence=observed,
        catalog=catalog,
    )
    objective = next(o for o in plan.objectives if o.id == "text-blocks")
    assert objective.completion.kind == "finding"
    assert objective.completion.finding_ids == ["java:S6126"]


def test_gradle_modes_are_blocked_until_artifact_pinning_is_supported(tmp_path):
    observed = evidence(tmp_path)
    observed.snapshot.build_system = BuildSystem.GRADLE
    plan = resolve_intent(
        tmp_path,
        17,
        "minimal",
        Settings(),
        planner=Planner(),
        evidence=observed,
        catalog=RecipeCatalogSession([]),
    )
    assert any("Gradle" in reason for reason in plan.blocked_reasons)


def test_invalid_model_destination_is_retried_with_validation_feedback(tmp_path):
    class RetryPlanner:
        def __init__(self):
            self.requests = []

        def resolve(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return DestinationChoices(
                    spring_boot_version="3.5.16", rationale="Invalid for plain Java"
                )
            assert request.validation_feedback
            return DestinationChoices(rationale="Retain plain Java")

    planner = RetryPlanner()
    plan = resolve_intent(
        tmp_path,
        17,
        "recommended",
        Settings(),
        planner=planner,
        evidence=evidence(tmp_path),
        catalog=RecipeCatalogSession([]),
    )
    assert not plan.blocked_reasons
    assert len(planner.requests) == 2


def test_minimal_selects_smallest_known_compatible_framework_change(tmp_path):
    observed = evidence(tmp_path)
    observed.snapshot.spring_boot_version = "2.6.2"
    observed.module_versions["."]["spring_boot"] = "2.6.2"
    observed.owners = {
        "org.springframework.boot:spring-boot-starter-parent": {
            "kind": "parent",
            "version": "2.6.2",
        }
    }

    class AggressivePlanner:
        def resolve(self, request):
            return DestinationChoices(spring_boot_version="3.5.16", rationale="Major upgrade")

    catalog = RecipeCatalogSession(
        [
            RecipeSource(
                recipe="refinery.UpgradeSpringBoot",
                display_name="Boot",
                description="Pinned",
                repository="refinery",
                file_path="wrapper",
                artifact="org.openrewrite.recipe:rewrite-migrate-java:3.42.1",
            )
        ]
    )
    plan = resolve_intent(
        tmp_path,
        17,
        "minimal",
        Settings(),
        planner=AggressivePlanner(),
        evidence=observed,
        catalog=catalog,
    )
    assert next(t for t in plan.targets if t.component == "spring_boot").version == "2.7.18"


def test_invalid_source_evidence_feedback_identifies_objective_and_valid_ids(tmp_path):
    from refinery_ai.modernization.models import CompletionPredicate, ModernizationObjective

    class InvalidEvidencePlanner:
        def __init__(self):
            self.feedback = None

        def resolve(self, request):
            self.feedback = request.validation_feedback
            return DestinationChoices(
                rationale="Improve source",
                objectives=[
                    ModernizationObjective(
                        id="cleanup-imports",
                        category="source",
                        rationale="Unused imports",
                        evidence_ids=["java:S1128"],
                        completion=CompletionPredicate(kind="receipt"),
                    )
                ],
            )

    planner = InvalidEvidencePlanner()
    plan = resolve_intent(
        tmp_path,
        17,
        "full",
        Settings(),
        planner=planner,
        evidence=evidence(tmp_path),
        catalog=RecipeCatalogSession([]),
    )
    assert plan.blocked_reasons
    assert "cleanup-imports" in planner.feedback
    assert "java:S1128" in planner.feedback
    assert "discovery" in planner.feedback


def test_retry_can_correct_resolver_owned_objective_category(tmp_path):
    from refinery_ai.modernization.models import CompletionPredicate, ModernizationObjective

    class CorrectingPlanner:
        def resolve(self, request):
            if request.validation_feedback:
                assert "upgrade-java" in request.validation_feedback
                assert "compatibility" in request.validation_feedback
                assert "generated by the resolver" in request.validation_feedback
                return DestinationChoices(rationale="Let the resolver generate the Java objective")
            return DestinationChoices(
                rationale="Upgrade Java",
                objectives=[
                    ModernizationObjective(
                        id="upgrade-java",
                        category="compatibility",
                        rationale="Upgrade Java",
                        evidence_ids=["discovery"],
                        completion=CompletionPredicate(kind="receipt"),
                    )
                ],
            )

    plan = resolve_intent(
        tmp_path,
        17,
        "full",
        Settings(),
        planner=CorrectingPlanner(),
        evidence=evidence(tmp_path),
        catalog=RecipeCatalogSession([]),
    )
    assert not plan.blocked_reasons
    assert [objective.id for objective in plan.objectives] == ["java"]
