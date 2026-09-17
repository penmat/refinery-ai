import os
import subprocess
from pathlib import Path

from refinery_ai.migrations.openrewrite import _infer_recipe_artifact
from refinery_ai.migrations.repair import MigrationRepairStage
from refinery_ai.migrations.runner import MigrationRunner, migration_display_name
from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    ExecutionType,
    PersistedMigrationPlan,
    RecipeRecommendation,
    RunStatus,
    VerificationResult,
)
from refinery_ai.planning_artifacts import MigrationPlanStore


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, int]] = []

    def run(
        self, snapshot, step, *, apply: bool, timeout: int, environment=None
    ) -> VerificationResult:
        del environment
        self.calls.append((step.recipe, apply, timeout))
        if apply:
            Path(snapshot.repository, "migration-output.txt").write_text(step.recipe + "\n")
        return VerificationResult(command=["rewrite"], exit_code=0, duration_seconds=0)


def initialize_git_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "README.md").write_text("fixture\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, env=env, check=True)


def test_migration_runner_loads_latest_plan_and_dispatches_by_type(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.UpgradeToJava17",
        reason="Upgrade Java.",
        evidence=["goal.target_java=17"],
        expected_benefit="Java 17 support.",
        priority="high",
        confidence=0.9,
    )
    plan = PersistedMigrationPlan(
        plan_id="plan1",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[step],
    )
    (store.root / "plan1.json").write_text(plan.model_dump_json())
    runner = RecordingRunner()
    builds: list[list[str]] = []
    progress: list[str] = []

    def build_verifier(repository, command, **kwargs):
        builds.append(command)
        return VerificationResult(command=command, exit_code=0, duration_seconds=0)

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: runner},
        timeout=42,
        build_verifier=build_verifier,
        progress=progress.append,
    ).run(apply=True)

    assert result.status is RunStatus.COMPLETED
    assert runner.calls == [(step.recipe, True, 42)]
    assert builds == [["mvn", "clean", "test"], ["mvn", "clean", "test"]]
    assert result.baseline_verification is not None
    assert result.baseline_verification.green
    assert result.steps[0].execution is not None
    assert result.steps[0].verification is not None
    assert progress == [
        "Reading plan plan1 ... about to run 1 migration.",
        "Preparing isolated migration worktrees ...",
        "Verifying baseline before Migration 1 ...",
        "Running Migration 1/1: Upgrade to Java 17",
        "Verifying Migration 1/1: Upgrade to Java 17",
        "Migration 1/1 completed: Upgrade to Java 17",
        "Applying the last verified checkpoint to the source checkout ...",
        f"Applied verified result: {result.published_revision[:12]}",
    ]


def test_migration_runner_reuses_matching_verified_checkpoint_as_baseline(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    starting_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="example.Upgrade",
        reason="Upgrade.",
        evidence=["test"],
        expected_benefit="upgrade",
        priority="high",
        confidence=1,
    )
    plan = PersistedMigrationPlan(
        plan_id="trustedbaseline",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[step],
    )
    (store.root / "trustedbaseline.json").write_text(plan.model_dump_json())
    builds: list[list[str]] = []
    progress: list[str] = []

    def build_verifier(repository, command, **kwargs):
        del repository, kwargs
        builds.append(command)
        return VerificationResult(command=command, exit_code=0, duration_seconds=0)

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: RecordingRunner()},
        build_verifier=build_verifier,
        progress=progress.append,
    ).run(
        "trustedbaseline",
        apply=True,
        trusted_baseline_revision=starting_revision,
        trusted_baseline_java=8,
    )

    assert result.status is RunStatus.COMPLETED
    assert builds == [["mvn", "clean", "test"]]
    assert result.verified_java == 8
    assert any("skipping baseline build" in message for message in progress)


def test_migration_runner_uses_green_target_jdk_for_java_upgrade(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.UpgradeToJava17",
        reason="Upgrade Java.",
        evidence=["goal.target_java=17"],
        expected_benefit="Java 17 support.",
        priority="high",
        confidence=0.9,
    )
    plan = PersistedMigrationPlan(
        plan_id="jdkswitch",
        snapshot=DiscoverySnapshot(
            repository=tmp_path,
            build_system=BuildSystem.MAVEN,
            java_version="11",
        ),
        steps=[step],
    )
    (store.root / "jdkswitch.json").write_text(plan.model_dump_json())
    runner_environments: list[dict[str, str] | None] = []
    build_environments: list[dict[str, str] | None] = []

    class Runner:
        def run(self, snapshot, step, *, apply, timeout, environment=None):
            del step, timeout
            runner_environments.append(environment)
            if apply:
                Path(snapshot.repository, "migration-output.txt").write_text("changed")
            return VerificationResult(command=["rewrite"], exit_code=0, duration_seconds=0)

    class Resolver:
        def environment(self, major):
            return {"JAVA_HOME": f"/jdk/{major}", "PATH": f"/jdk/{major}/bin"}

    def build_verifier(repository, command, **kwargs):
        del repository
        build_environments.append(kwargs["environment"])
        return VerificationResult(command=command, exit_code=0, duration_seconds=0)

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: Runner()},
        build_verifier=build_verifier,
        jdk_resolver=Resolver(),  # type: ignore[arg-type]
    ).run("jdkswitch", apply=True)

    assert result.status is RunStatus.COMPLETED
    assert runner_environments[0]["JAVA_HOME"] == "/jdk/17"
    assert [environment["JAVA_HOME"] for environment in build_environments] == [
        "/jdk/17",
        "/jdk/17",
    ]


def test_boot_3_recipe_verifies_with_java_17_after_legacy_baseline(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        reason="Upgrade Boot.",
        evidence=["spring_boot_version=2.7.18"],
        expected_benefit="Boot 3.",
        priority="high",
        confidence=1,
    )
    plan = PersistedMigrationPlan(
        plan_id="boot3jdkswitch",
        snapshot=DiscoverySnapshot(
            repository=tmp_path,
            build_system=BuildSystem.MAVEN,
            java_version="1.8",
        ),
        steps=[step],
    )
    (store.root / "boot3jdkswitch.json").write_text(plan.model_dump_json())
    runner_environments: list[dict[str, str] | None] = []
    build_environments: list[str] = []

    class Runner:
        def run(self, snapshot, step, *, apply, timeout, environment=None):
            del step, timeout
            runner_environments.append(environment)
            if apply:
                Path(snapshot.repository, "migration-output.txt").write_text("changed")
            return VerificationResult(command=["rewrite"], exit_code=0, duration_seconds=0)

    class Resolver:
        def environment(self, major):
            return {"JAVA_HOME": f"/jdk/{major}", "PATH": f"/jdk/{major}/bin"}

    def build_verifier(repository, command, **kwargs):
        del repository
        java_home = kwargs["environment"]["JAVA_HOME"]
        build_environments.append(java_home)
        # The pre-migration source only builds on JDK 8; the migrated source is
        # then expected to build on the composite recipe's implicit JDK 17 target.
        baseline_attempt = len(build_environments) <= 3
        exit_code = 0 if java_home == "/jdk/8" or not baseline_attempt else 1
        return VerificationResult(command=command, exit_code=exit_code, duration_seconds=0)

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: Runner()},
        build_verifier=build_verifier,
        jdk_resolver=Resolver(),  # type: ignore[arg-type]
    ).run("boot3jdkswitch", apply=True)

    assert result.status is RunStatus.COMPLETED
    assert build_environments == ["/jdk/17", "/jdk/11", "/jdk/8", "/jdk/17"]
    assert runner_environments[0]["JAVA_HOME"] == "/jdk/8"


def test_migration_runner_falls_back_from_java_target_to_source_jdk(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.Java8ToJava11",
        reason="Upgrade Java.",
        evidence=["java.version=1.8"],
        expected_benefit="Java 11 support.",
        priority="high",
        confidence=0.9,
    )
    plan = PersistedMigrationPlan(
        plan_id="jdkfallback",
        snapshot=DiscoverySnapshot(
            repository=tmp_path,
            build_system=BuildSystem.MAVEN,
            java_version="1.8",
        ),
        steps=[step],
    )
    (store.root / "jdkfallback.json").write_text(plan.model_dump_json())
    runner_environments: list[dict[str, str] | None] = []
    build_environments: list[str] = []

    class Runner:
        def run(self, snapshot, step, *, apply, timeout, environment=None):
            del step, timeout
            runner_environments.append(environment)
            if apply:
                Path(snapshot.repository, "migration-output.txt").write_text("changed")
            return VerificationResult(command=["rewrite"], exit_code=0, duration_seconds=0)

    class Resolver:
        def environment(self, major):
            return {"JAVA_HOME": f"/jdk/{major}", "PATH": f"/jdk/{major}/bin"}

    def build_verifier(repository, command, **kwargs):
        del repository
        home = kwargs["environment"]["JAVA_HOME"]
        build_environments.append(home)
        return VerificationResult(
            command=command,
            exit_code=0 if home == "/jdk/8" or len(build_environments) == 3 else 1,
            duration_seconds=0,
        )

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: Runner()},
        build_verifier=build_verifier,
        jdk_resolver=Resolver(),  # type: ignore[arg-type]
    ).run("jdkfallback", apply=True)

    assert result.status is RunStatus.COMPLETED
    assert build_environments == ["/jdk/11", "/jdk/8", "/jdk/11"]
    assert runner_environments[0]["JAVA_HOME"] == "/jdk/8"


def test_migration_runner_stops_at_recipe_with_failing_post_build(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    first = RecipeRecommendation(
        recipe="recipe.one",
        reason="First.",
        evidence=["first"],
        expected_benefit="First benefit.",
        priority="high",
        confidence=0.9,
    )
    second = first.model_copy(update={"recipe": "recipe.two"})
    plan = PersistedMigrationPlan(
        plan_id="plan2",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[first, second],
    )
    (store.root / "plan2.json").write_text(plan.model_dump_json())
    runner = RecordingRunner()
    build_count = 0

    def build_verifier(repository, command, **kwargs):
        nonlocal build_count
        build_count += 1
        return VerificationResult(
            command=command, exit_code=0 if build_count < 3 else 1, duration_seconds=0
        )

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: runner},
        build_verifier=build_verifier,
    ).run("plan2", apply=True)

    assert result.status is RunStatus.FAILED
    assert build_count == 3
    assert result.steps[0].status is RunStatus.COMPLETED
    assert result.steps[1].message == "Post-migration build failed: recipe.two"


def test_migration_runner_repairs_residual_springfox_before_stopping(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
        reason="Upgrade Boot.",
        evidence=["boot"],
        expected_benefit="Boot 2.7.",
        priority="high",
        confidence=1,
    )
    plan = PersistedMigrationPlan(
        plan_id="repair",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[step],
    )
    (store.root / "repair.json").write_text(plan.model_dump_json())

    class SpringfoxRunner:
        def run(self, snapshot, step, *, apply: bool, timeout: int, environment=None):
            del environment
            source = Path(snapshot.repository, "src/main/java/SwaggerConfiguration.java")
            source.parent.mkdir(parents=True)
            source.write_text("import springfox.documentation.spring.web.plugins.Docket;\n")
            return VerificationResult(command=["rewrite"], exit_code=0, duration_seconds=0)

    class Repairer:
        def repair(self, repository, step, failure):
            Path(repository, "src/main/java/SwaggerConfiguration.java").write_text(
                "class SwaggerConfiguration {}\n"
            )
            return VerificationResult(command=["repair"], exit_code=0, duration_seconds=0)

    def verifier(repository, command, **kwargs):
        source = Path(repository, "src/main/java/SwaggerConfiguration.java")
        red = source.is_file() and "springfox." in source.read_text()
        return VerificationResult(command=command, exit_code=int(red), duration_seconds=0)

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: SpringfoxRunner()},
        build_verifier=verifier,
        repairer=Repairer(),
    ).run(apply=True)

    assert result.status is RunStatus.COMPLETED
    assert len(result.steps[0].repairs) == 1
    assert result.steps[0].verification is not None
    assert result.steps[0].verification.green


def test_migration_runner_budgets_deterministic_and_ai_repairs_separately(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
        reason="Replace Springfox.",
        evidence=["springfox"],
        expected_benefit="Use Springdoc.",
        priority="high",
        confidence=1,
    )
    plan = PersistedMigrationPlan(
        plan_id="staged-repair",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[step],
    )
    (store.root / "staged-repair.json").write_text(plan.model_dump_json())

    class SpringdocRunner:
        def run(self, snapshot, step, *, apply: bool, timeout: int, environment=None):
            del step, timeout, environment
            if apply:
                Path(snapshot.repository, "pom.xml").write_text(
                    "<artifactId>openapi-generator-maven-plugin</artifactId>\n"
                )
            return VerificationResult(command=["rewrite"], exit_code=0, duration_seconds=0)

    calls = {"deterministic": 0, "ai": 0}

    class DeterministicRepairer:
        def repair(self, repository, step, failure):
            del repository, step, failure
            calls["deterministic"] += 1
            return VerificationResult(
                command=["deterministic-repair"], exit_code=0, duration_seconds=0
            )

    class AiRepairer:
        def repair(self, repository, step, failure):
            del repository, step, failure
            calls["ai"] += 1
            return VerificationResult(command=["ai-repair"], exit_code=0, duration_seconds=0)

    build_count = 0

    def verifier(repository, command, **kwargs):
        nonlocal build_count
        del repository, kwargs
        build_count += 1
        green = build_count == 1 or calls["ai"] == 1
        return VerificationResult(
            command=command,
            exit_code=0 if green else 1,
            stdout=(
                "package io.swagger.annotations does not exist"
                if not green and calls["deterministic"] < 3
                else "incompatible types: boolean cannot be converted"
                if not green
                else ""
            ),
            duration_seconds=0,
        )

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: SpringdocRunner()},
        build_verifier=verifier,
        repair_stages=(
            MigrationRepairStage("deterministic", DeterministicRepairer(), 3),
            MigrationRepairStage("AI", AiRepairer(), 3, uses_model=True),
        ),
    ).run(apply=True)

    assert result.status is RunStatus.COMPLETED
    assert calls == {"deterministic": 3, "ai": 1}
    assert [repair.command for repair in result.steps[0].repairs] == [
        ["refinery", "deterministic-repair"],
        ["refinery", "deterministic-repair"],
        ["refinery", "deterministic-repair"],
        ["refinery", "ai-repair"],
    ]


def test_migration_runner_rolls_back_ai_repairs_that_do_not_improve_verification(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
        reason="Replace Springfox.",
        evidence=["springfox"],
        expected_benefit="Use Springdoc.",
        priority="high",
        confidence=1,
    )
    plan = PersistedMigrationPlan(
        plan_id="transactional-ai-repair",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[step],
    )
    (store.root / "transactional-ai-repair.json").write_text(plan.model_dump_json())

    class SpringdocRunner:
        def run(self, snapshot, step, *, apply: bool, timeout: int, environment=None):
            del step, timeout, environment
            if apply:
                Path(snapshot.repository, "contract.yml").write_text("one-failure")
            return VerificationResult(command=["rewrite"], exit_code=0, duration_seconds=0)

    class TransactionalAiRepairer:
        def __init__(self) -> None:
            self.previous = ""
            self.target: Path | None = None
            self.starts: list[str] = []
            self.rollbacks = 0

        def repair(self, repository, step, failure):
            del step, failure
            contract = Path(repository, "contract.yml")
            self.target = contract
            self.previous = contract.read_text()
            self.starts.append(self.previous)
            contract.write_text("five-failures")
            return VerificationResult(command=["ai-repair"], exit_code=0, duration_seconds=0)

        def accept_last(self) -> None:
            self.previous = ""

        def rollback_last(self) -> None:
            self.rollbacks += 1
            assert self.target is not None
            self.target.write_text(self.previous)
            self.previous = ""

    repairer = TransactionalAiRepairer()
    build_count = 0

    def verifier(repository, command, **kwargs):
        nonlocal build_count
        del command, kwargs
        build_count += 1
        if build_count == 1:
            return VerificationResult(command=["test"], exit_code=0, duration_seconds=0)
        content = Path(repository, "contract.yml").read_text()
        failures = 5 if content == "five-failures" else 1
        return VerificationResult(
            command=["test"],
            exit_code=1,
            stdout=f"Tests run: 10, Failures: {failures}, Errors: 0, Skipped: 0",
            duration_seconds=0,
        )

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: SpringdocRunner()},
        build_verifier=verifier,
        repair_stages=(MigrationRepairStage("AI", repairer, 2, uses_model=True),),
    ).run(apply=True)

    assert result.status is RunStatus.FAILED
    assert repairer.starts == ["one-failure", "one-failure"]
    assert repairer.rollbacks == 2
    assert result.steps[0].verification is not None
    assert "Failures: 1" in result.steps[0].verification.stdout


def test_migration_runner_stops_before_recipes_when_baseline_is_red(tmp_path: Path) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="recipe.one",
        reason="First.",
        evidence=["first"],
        expected_benefit="First benefit.",
        priority="high",
        confidence=0.9,
    )
    plan = PersistedMigrationPlan(
        plan_id="redbaseline",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[step],
    )
    (store.root / "redbaseline.json").write_text(plan.model_dump_json())
    runner = RecordingRunner()
    progress: list[str] = []

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: runner},
        build_verifier=lambda repository, command, **kwargs: VerificationResult(
            command=command,
            exit_code=1,
            stdout="existing test failure",
            duration_seconds=0,
        ),
        progress=progress.append,
    ).run(apply=True)

    assert result.status is RunStatus.FAILED
    assert result.steps == []
    assert runner.calls == []
    assert result.baseline_verification is not None
    assert not result.baseline_verification.green
    assert result.baseline_artifacts is not None
    assert (result.baseline_artifacts / "baseline.json").is_file()
    assert (
        "not a migration regression"
        in (result.baseline_artifacts / "failure-summary.md").read_text()
    )
    assert "Baseline build failed; no migrations were run" in progress[-1]
    assert result.published_revision is None


def test_first_failed_migration_does_not_publish_unchanged_starting_revision(
    tmp_path: Path,
) -> None:
    initialize_git_repository(tmp_path)
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    step = RecipeRecommendation(
        recipe="recipe.one",
        reason="First.",
        evidence=["first"],
        expected_benefit="First benefit.",
        priority="high",
        confidence=0.9,
    )
    plan = PersistedMigrationPlan(
        plan_id="firstfailure",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[step],
    )
    (store.root / "firstfailure.json").write_text(plan.model_dump_json())
    verification_count = 0
    progress: list[str] = []

    def verifier(repository, command, **kwargs):
        nonlocal verification_count
        verification_count += 1
        return VerificationResult(
            command=command,
            exit_code=0 if verification_count == 1 else 1,
            duration_seconds=0,
        )

    result = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: RecordingRunner()},
        build_verifier=verifier,
        progress=progress.append,
    ).run(apply=True)

    assert result.status is RunStatus.FAILED
    assert result.published_revision is None
    assert not any(message.startswith("Applied verified result:") for message in progress)
    assert not any(
        message.startswith("Applying the last verified checkpoint") for message in progress
    )


def test_old_spring_plan_infers_rewrite_spring_artifact() -> None:
    assert _infer_recipe_artifact("org.openrewrite.java.springdoc.SpringFoxToSpringDoc") == (
        "org.openrewrite.recipe:rewrite-spring:RELEASE"
    )


def test_migration_display_name_formats_java_and_camel_case_recipes() -> None:
    assert migration_display_name("org.openrewrite.Java8toJava11") == "Java 8 → Java 11"
    assert migration_display_name("org.openrewrite.SpringFoxToSpringDoc") == (
        "Spring Fox → Spring Doc"
    )
