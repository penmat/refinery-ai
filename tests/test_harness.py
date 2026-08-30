import subprocess
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel

from refinery_ai.context import DurableContextStore
from refinery_ai.harness import (
    AssessmentHarnessPolicy,
    HarnessPolicyError,
    HarnessRepositoryEnvironment,
    create_read_only_assessment_harness,
)
from refinery_ai.models import BuildSystem, DiscoverySnapshot
from refinery_ai.repository import RepositoryEnvironment


def test_assessment_factory_is_read_only_and_has_no_shell(tmp_path: Path) -> None:
    bundle = create_read_only_assessment_harness(TestModel(), tmp_path)

    assert bundle.filesystem.read_only
    assert not bundle.policy.network_allowed
    assert bundle.repo_context.workspace_dir == tmp_path
    assert bundle.agent is not None
    assert bundle.compaction.max_messages == bundle.policy.context_clear_after_messages
    assert len(bundle.subagents.agents) == 1
    assert bundle.subagents.agents[0].max_calls == 1
    assert not bundle.subagents.inherit_tools
    assert len(bundle.subagents.shared_capabilities) == 2


@pytest.mark.asyncio
async def test_harness_reads_line_addressable_evidence_and_finds_files(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("first\nsecond\n")
    environment = HarnessRepositoryEnvironment(tmp_path)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    evidence = await environment.read_file("pom.xml")
    found = await environment.find_files("pom.xml")

    assert "1\tfirst" in evidence and "2\tsecond" in evidence
    assert "pom.xml" in found
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before


@pytest.mark.asyncio
async def test_harness_denies_escape_and_secret_reads(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=value")
    environment = HarnessRepositoryEnvironment(tmp_path)

    with pytest.raises(ModelRetry, match="outside the root"):
        await environment.read_file("../outside")
    with pytest.raises(ModelRetry, match="denied"):
        await environment.read_file(".env")


@pytest.mark.asyncio
async def test_large_evidence_is_bounded_and_durably_addressable(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "large.txt").write_text("\n".join(f"line-{number:04}" for number in range(100)))
    store = DurableContextStore(tmp_path / "context-artifacts")
    environment = HarnessRepositoryEnvironment(
        repository,
        policy=AssessmentHarnessPolicy(tool_output_limit_characters=120),
        context_store=store,
    )

    preview, receipt = await environment.read_bounded_file("large.txt")

    assert receipt is not None
    assert len(preview) <= 160
    assert "line-0000" in store.read(receipt)
    assert not (repository / "context-artifacts").exists()


def test_harness_environment_satisfies_refinery_protocol(tmp_path: Path) -> None:
    environment: RepositoryEnvironment = HarnessRepositoryEnvironment(tmp_path)
    snapshot = environment.discover(tmp_path)

    assert isinstance(snapshot, DiscoverySnapshot)
    assert snapshot.build_system is BuildSystem.UNKNOWN


def test_harness_discovers_non_utf8_application_properties(tmp_path: Path) -> None:
    resources = tmp_path / "src/main/resources"
    resources.mkdir(parents=True)
    properties = resources / "application.properties"
    properties.write_bytes("welcome=caf\xe9\n".encode("latin-1"))

    snapshot = HarnessRepositoryEnvironment(tmp_path).discover(tmp_path)

    assert snapshot.non_utf8_application_properties == [
        Path("src/main/resources/application.properties")
    ]


def test_harness_investigator_matches_golden_fixture_without_writes() -> None:
    repository = Path(__file__).parent / "fixtures/legacy-java"
    before = {path.relative_to(repository) for path in repository.rglob("*")}

    harness = HarnessRepositoryEnvironment(repository).discover(repository)

    assert harness.build_system is BuildSystem.MAVEN
    assert harness.build_files == [Path("pom.xml")]
    assert harness.java_version == "11"
    assert harness.frameworks == ["Spring"]
    assert harness.modules[0].java_files == 1
    assert {path.relative_to(repository) for path in repository.rglob("*")} == before


@pytest.mark.parametrize(
    "command",
    [
        ["git", "reset", "--hard"],
        ["find", ".", "-delete"],
        ["sed", "-i", "s/a/b/", "pom.xml"],
        ["sh", "-c", "touch owned"],
        ["mvn", "deploy"],
        ["git", "status", "$(touch owned)"],
    ],
)
def test_assessment_shell_denies_mutating_or_unapproved_commands(command: list[str]) -> None:
    with pytest.raises(HarnessPolicyError):
        AssessmentHarnessPolicy().authorize(command)


def test_harness_shell_runs_allowed_command_and_preserves_failure(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    environment = HarnessRepositoryEnvironment(tmp_path)
    success = environment.verify(
        tmp_path, ["git", "status", "--short"], timeout=5, output_limit=100
    )
    failure = environment.verify(tmp_path, ["git", "show", "missing"], timeout=5, output_limit=100)

    assert success.exit_code == 0
    assert failure.exit_code != 0
    assert failure.duration_seconds >= 0


def test_harness_shell_enforces_timeout_and_output_cap(tmp_path: Path) -> None:
    policy = AssessmentHarnessPolicy(
        command_timeout_seconds=1, allowed_executables=frozenset({"printf", "sleep"})
    )
    environment = HarnessRepositoryEnvironment(tmp_path, policy=policy)

    large = environment.verify(tmp_path, ["printf", "x" * 200], timeout=5, output_limit=20)
    timed_out = environment.verify(tmp_path, ["sleep", "2"], timeout=5, output_limit=100)

    assert large.exit_code == 0 and large.stdout == "x" * 20
    assert timed_out.exit_code == 124


def test_assessment_policy_rejects_writable_or_network_configuration(tmp_path: Path) -> None:
    with pytest.raises(HarnessPolicyError):
        HarnessRepositoryEnvironment(
            tmp_path, policy=AssessmentHarnessPolicy(filesystem_read_only=False)
        )
    with pytest.raises(HarnessPolicyError):
        HarnessRepositoryEnvironment(tmp_path, policy=AssessmentHarnessPolicy(network_allowed=True))
