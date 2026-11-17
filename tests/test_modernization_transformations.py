import pytest

from refinery_ai.models import RecipeRecommendation
from refinery_ai.modernization.transformations import execution_identity, validate_options


def step(**changes):
    return RecipeRecommendation(
        recipe="org.openrewrite.maven.UpgradeDependencyVersion",
        reason="fix",
        evidence=["pom.xml"],
        expected_benefit="fix",
        priority="high",
        confidence=1,
        **changes,
    )


def test_maven_and_gradle_exact_dependency_options():
    for recipe in (
        "org.openrewrite.maven.UpgradeDependencyVersion",
        "org.openrewrite.gradle.UpgradeDependencyVersion",
    ):
        validate_options(recipe, {"groupId": "g", "artifactId": "a", "newVersion": "1.2.3"})


def test_unknown_options_rejected():
    with pytest.raises(ValueError):
        validate_options("org.openrewrite.maven.UpgradeDependencyVersion", {"command": "rm"})


def test_dynamic_or_unowned_target_rejected():
    for version in ("latest", "1.+", "${version}", "RELEASE"):
        with pytest.raises(ValueError):
            validate_options(
                "org.openrewrite.maven.UpgradeDependencyVersion",
                {"groupId": "g", "artifactId": "a", "newVersion": version},
            )


def test_same_recipe_different_coordinates_different_identity():
    assert execution_identity(
        step(options={"groupId": "g", "artifactId": "a", "newVersion": "1"})
    ) != execution_identity(step(options={"groupId": "g", "artifactId": "b", "newVersion": "1"}))


def test_wrapper_is_scoped_and_removed(tmp_path, monkeypatch):
    import yaml

    import refinery_ai.openrewrite as module
    from refinery_ai.models import BuildSystem, DiscoverySnapshot, VerificationResult
    from refinery_ai.openrewrite import OpenRewriteAdapter

    seen = []

    def verify(root, command, **kwargs):
        from pathlib import Path

        path = Path(next(v.split("=", 1)[1] for v in command if "configLocation=" in v))
        payload = yaml.safe_load(path.read_text())
        assert (
            payload["recipeList"][0]["org.openrewrite.maven.UpgradeDependencyVersion"]["newVersion"]
            == "1.2.3"
        )
        seen.append(path)
        return VerificationResult(
            command=command, exit_code=0, stdout="", stderr="", duration_seconds=0
        )

    monkeypatch.setattr(module, "verify", verify)
    recipe = "org.openrewrite.maven.UpgradeDependencyVersion"
    OpenRewriteAdapter(frozenset({recipe})).run(
        DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        recipe,
        options={"groupId": "g", "artifactId": "a", "newVersion": "1.2.3"},
    )
    assert seen and not seen[0].exists()


def test_framework_wrapper_pins_parent_and_compatibility_source_changes():
    from refinery_ai.modernization.transformations import recipe_entries

    entries = recipe_entries(
        "refinery.UpgradeSpringBoot",
        {
            "groupId": "org.springframework.boot",
            "artifactId": "spring-boot-starter-parent",
            "newVersion": "3.5.16",
            "migrateJakarta": True,
        },
    )
    assert entries[0]["org.openrewrite.maven.UpgradeParentVersion"]["newVersion"] == "3.5.16"
    changes = [entry["org.openrewrite.java.ChangePackage"] for entry in entries[1:]]
    assert {c["oldPackageName"] for c in changes} >= {"javax.persistence", "javax.servlet"}
    assert all(not c["recursive"] for c in changes if c["oldPackageName"] == "javax.transaction")
