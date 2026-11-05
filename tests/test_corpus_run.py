import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from refinery_ai.cli import app
from refinery_ai.corpus_run import (
    CorpusProjectResult,
    CorpusRunner,
    load_attempted_project_ids,
    load_corpus,
    load_failed_project_ids,
)


def _corpus(path: Path) -> Path:
    path.write_text(
        """\
schema_version: 1
corpus:
  id: sample
projects:
  - id: first
    name: First
    repo_url: https://example.com/first.git
    tier: easy
    ref: v2.6.2
    test_configuration_needed: true
    baseline_test: true
    baseline_migrations:
      - org.example.UpgradeOne
      - org.example.UpgradeTwo
  - id: second
    name: Second
    repo_url: https://example.com/second.git
    tier: normal
"""
    )
    return path


def test_corpus_runner_uses_ai_repair_by_default(tmp_path: Path, monkeypatch: Any) -> None:
    corpus = load_corpus(_corpus(tmp_path / "corpus.yaml"))
    commands: list[list[str]] = []

    def execute(command: list[str], log: Any, *, timeout: int) -> tuple[int, None]:
        commands.append(command)
        return 0, None

    monkeypatch.setattr(CorpusRunner, "_execute", staticmethod(execute))
    monkeypatch.setattr(CorpusRunner, "_revision", staticmethod(lambda repository: "abc123"))
    monkeypatch.setattr(
        CorpusRunner, "_profile_state", lambda self, repository, profile=None: (2, True)
    )
    result = CorpusRunner(corpus, tmp_path / "run", profile="profile").run([corpus.projects[0]])

    assert result.projects[0].status == "completed"
    assert result.projects[0].source_revision == "abc123"
    assert result.projects[0].requested_ref == "v2.6.2"
    assert result.projects[0].profile_cycles == 2
    assert result.profiles == ["profile"]
    assert [stage.profile for stage in result.projects[0].profile_stages] == ["profile"]
    assert result.projects[0].tier == "easy"
    assert result.projects[0].baseline_test is True
    assert corpus.projects[0].baseline_migrations == [
        "org.example.UpgradeOne",
        "org.example.UpgradeTwo",
    ]
    assert corpus.projects[1].baseline_test is False
    assert commands[0][:3] == ["git", "clone", "--quiet"]
    assert commands[1][-2:] == ["--quiet", "v2.6.2"]
    assert "--repair-mode" in commands[2]
    assert commands[2][commands[2].index("--repair-mode") + 1] == "configured-provider"
    assert (tmp_path / "run" / "summary.json").is_file()


def test_corpus_runner_applies_profiles_in_order_to_the_same_checkout(
    tmp_path: Path, monkeypatch: Any
) -> None:
    corpus = load_corpus(_corpus(tmp_path / "corpus.yaml"))
    commands: list[list[str]] = []
    revisions = iter(["source", "source", "java17", "java17", "java21"])

    def execute(command: list[str], log: Any, *, timeout: int) -> tuple[int, None]:
        commands.append(command)
        return 0, None

    monkeypatch.setattr(CorpusRunner, "_execute", staticmethod(execute))
    monkeypatch.setattr(CorpusRunner, "_revision", staticmethod(lambda repository: next(revisions)))
    monkeypatch.setattr(
        CorpusRunner, "_profile_state", lambda self, repository, profile=None: (1, True)
    )

    result = CorpusRunner(
        corpus,
        tmp_path / "run",
        profiles=["java17", "java21"],
    ).run([corpus.projects[1]])

    run_commands = commands[1:]
    assert [command[command.index("--profile") + 1] for command in run_commands] == [
        "java17",
        "java21",
    ]
    project = result.projects[0]
    assert project.status == "completed"
    assert project.result_revision == "java21"
    assert [stage.source_revision for stage in project.profile_stages] == ["source", "java17"]
    assert [stage.result_revision for stage in project.profile_stages] == ["java17", "java21"]


def test_corpus_runner_stops_profile_pipeline_after_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    corpus = load_corpus(_corpus(tmp_path / "corpus.yaml"))
    commands: list[list[str]] = []
    exit_codes = iter([(0, None), (1, "java17 failed")])

    def execute(command: list[str], log: Any, *, timeout: int) -> tuple[int, str | None]:
        commands.append(command)
        return next(exit_codes)

    monkeypatch.setattr(CorpusRunner, "_execute", staticmethod(execute))
    monkeypatch.setattr(CorpusRunner, "_revision", staticmethod(lambda repository: "revision"))

    result = CorpusRunner(
        corpus,
        tmp_path / "run",
        profiles=["java17", "java21"],
    ).run([corpus.projects[1]])

    assert len(commands) == 2
    assert result.projects[0].status == "failed"
    assert [stage.profile for stage in result.projects[0].profile_stages] == ["java17"]
    assert result.projects[0].profile_stages[0].error == "java17 failed"


def test_corpus_runner_runs_projects_concurrently_and_preserves_order(
    tmp_path: Path, monkeypatch: Any
) -> None:
    corpus = load_corpus(_corpus(tmp_path / "corpus.yaml"))
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def run_project(self: CorpusRunner, project: Any) -> CorpusProjectResult:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.04 if project.id == "first" else 0.01)
        with lock:
            active -= 1
        now = datetime.now(UTC)
        return CorpusProjectResult(
            id=project.id,
            name=project.name,
            repo_url=str(project.repo_url),
            status="completed",
            ai_patching=self.ai_patching,
            elapsed_seconds=0.01,
            started_at=now,
            finished_at=now,
            repository=str(tmp_path / project.id),
            log=str(tmp_path / f"{project.id}.log"),
        )

    monkeypatch.setattr(CorpusRunner, "_run_project", run_project)
    result = CorpusRunner(corpus, tmp_path / "parallel-run", profile="profile", jobs=2).run(
        corpus.projects
    )

    assert maximum_active == 2
    assert [project.id for project in result.projects] == ["first", "second"]
    assert result.jobs == 2


def test_run_corpus_can_disable_ai_and_select_projects(tmp_path: Path, monkeypatch: Any) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")
    calls: list[tuple[list[str], bool]] = []

    class FakeRunner:
        def __init__(self, corpus: Any, output: Path, **kwargs: Any) -> None:
            self.ai_patching = kwargs["ai_patching"]

        def run(self, projects: Any) -> Any:
            calls.append(([project.id for project in projects], self.ai_patching))
            return type("Result", (), {"projects": []})()

    monkeypatch.setattr("refinery_ai.cli.CorpusRunner", FakeRunner)
    result = CliRunner().invoke(
        app,
        [
            "run-corpus",
            str(corpus_path),
            "--project",
            "second",
            "--no-ai-patching",
            "--output-directory",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code == 0
    assert calls == [(["second"], False)]


def test_run_corpus_accepts_an_ordered_profile_pipeline(tmp_path: Path, monkeypatch: Any) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")
    calls: list[list[str]] = []

    class FakeRunner:
        def __init__(self, corpus: Any, output: Path, **kwargs: Any) -> None:
            calls.append(kwargs["profiles"])

        def run(self, projects: Any) -> Any:
            return type("Result", (), {"projects": []})()

    monkeypatch.setattr("refinery_ai.cli.CorpusRunner", FakeRunner)
    result = CliRunner().invoke(
        app,
        [
            "run-corpus",
            str(corpus_path),
            "--profile",
            "spring-boot-3.2-java-17",
            "--profile",
            "spring-boot-3.5-java-21",
        ],
    )

    assert result.exit_code == 0
    assert calls == [["spring-boot-3.2-java-17", "spring-boot-3.5-java-21"]]
    assert "spring-boot-3.2-java-17 -> spring-boot-3.5-java-21" in result.output


def test_run_corpus_skips_projects_needing_test_configuration_by_default(
    tmp_path: Path, monkeypatch: Any
) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")
    calls: list[list[str]] = []

    class FakeRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, projects: Any) -> Any:
            calls.append([project.id for project in projects])
            return type("Result", (), {"projects": []})()

    monkeypatch.setattr("refinery_ai.cli.CorpusRunner", FakeRunner)
    result = CliRunner().invoke(app, ["run-corpus", str(corpus_path)])

    assert result.exit_code == 0
    assert calls == [["second"]]
    assert "Skipping 1 project(s)" in result.output


def test_run_corpus_can_include_projects_needing_test_configuration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")
    calls: list[list[str]] = []

    class FakeRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, projects: Any) -> Any:
            calls.append([project.id for project in projects])
            return type("Result", (), {"projects": []})()

    monkeypatch.setattr("refinery_ai.cli.CorpusRunner", FakeRunner)
    result = CliRunner().invoke(
        app,
        ["run-corpus", str(corpus_path), "--include-test-configuration-needed"],
    )

    assert result.exit_code == 0
    assert calls == [["first", "second"]]


def test_run_corpus_can_select_only_baseline_tests(tmp_path: Path, monkeypatch: Any) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")
    calls: list[list[str]] = []

    class FakeRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, projects: Any) -> Any:
            calls.append([project.id for project in projects])
            return type("Result", (), {"projects": []})()

    monkeypatch.setattr("refinery_ai.cli.CorpusRunner", FakeRunner)
    result = CliRunner().invoke(
        app,
        [
            "run-corpus",
            str(corpus_path),
            "--baseline-tests-only",
            "--include-test-configuration-needed",
        ],
    )

    assert result.exit_code == 0
    assert calls == [["first"]]
    assert "Selected 1 baseline test project(s)." in result.output


def test_run_corpus_reports_when_no_baseline_tests_match(tmp_path: Path) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")

    result = CliRunner().invoke(
        app,
        ["run-corpus", str(corpus_path), "--baseline-tests-only", "--tier", "normal"],
    )

    assert result.exit_code == 2
    assert "No corpus projects matched the selection." in result.output


def test_load_failed_project_ids_accepts_run_directory(tmp_path: Path) -> None:
    run = tmp_path / "prior-run"
    run.mkdir()
    (run / "summary.json").write_text(
        '{"projects": ['
        '{"id": "first", "status": "failed"},'
        '{"id": "second", "status": "completed"},'
        '{"id": "third", "status": "timed-out"}'
        "]}\n"
    )

    assert load_failed_project_ids(run) == {"first", "third"}


def test_load_attempted_project_ids_combines_run_history(tmp_path: Path) -> None:
    history = tmp_path / "corpus-runs"
    for name, project_ids in (("run-1", ("first",)), ("run-2", ("second", "third"))):
        run = history / name
        run.mkdir(parents=True)
        projects = ",".join(
            f'{{"id": "{project_id}", "status": "failed"}}' for project_id in project_ids
        )
        (run / "summary.json").write_text(f'{{"projects": [{projects}]}}\n')

    assert load_attempted_project_ids(history) == {"first", "second", "third"}


def test_run_corpus_can_select_only_new_projects(tmp_path: Path, monkeypatch: Any) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")
    history = tmp_path / "history"
    prior = history / "prior"
    prior.mkdir(parents=True)
    (prior / "summary.json").write_text('{"projects": [{"id": "first", "status": "failed"}]}\n')
    calls: list[list[str]] = []

    class FakeRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, projects: Any) -> Any:
            calls.append([project.id for project in projects])
            return type("Result", (), {"projects": []})()

    monkeypatch.setattr("refinery_ai.cli.CorpusRunner", FakeRunner)
    result = CliRunner().invoke(
        app,
        ["run-corpus", str(corpus_path), "--only-new", "--run-history", str(history)],
    )

    assert result.exit_code == 0
    assert calls == [["second"]]
    assert "Selected 1 new project(s)" in result.output


def test_run_corpus_rejects_only_new_with_rerun_failed(tmp_path: Path) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")
    prior = tmp_path / "summary.json"
    prior.write_text('{"projects": []}\n')

    result = CliRunner().invoke(
        app,
        [
            "run-corpus",
            str(corpus_path),
            "--only-new",
            "--rerun-failed-from",
            str(prior),
        ],
    )

    assert result.exit_code == 2
    assert "cannot be combined" in result.output


def test_run_corpus_reruns_failures_but_still_skips_test_configuration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")
    prior_summary = tmp_path / "prior-summary.json"
    prior_summary.write_text(
        '{"projects": [{"id": "first", "status": "failed"},{"id": "second", "status": "failed"}]}\n'
    )
    calls: list[list[str]] = []

    class FakeRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, projects: Any) -> Any:
            calls.append([project.id for project in projects])
            return type("Result", (), {"projects": []})()

    monkeypatch.setattr("refinery_ai.cli.CorpusRunner", FakeRunner)
    result = CliRunner().invoke(
        app,
        ["run-corpus", str(corpus_path), "--rerun-failed-from", str(prior_summary)],
    )

    assert result.exit_code == 0
    assert calls == [["second"]]
    assert "Selected 2 unsuccessful project(s)" in result.output
    assert "Skipping 1 project(s)" in result.output


def test_run_corpus_rejects_unknown_project(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["run-corpus", str(_corpus(tmp_path / "corpus.yaml")), "--project", "missing"],
    )

    assert result.exit_code == 2
    assert "Unknown project ID(s): missing" in result.output


def test_corpus_runner_distinguishes_checkout_failure(tmp_path: Path, monkeypatch: Any) -> None:
    corpus = load_corpus(_corpus(tmp_path / "corpus.yaml"))
    exit_codes = iter([(0, None), (1, "unknown ref")])
    monkeypatch.setattr(CorpusRunner, "_execute", lambda *args, **kwargs: next(exit_codes))

    result = CorpusRunner(corpus, tmp_path / "run", profile="profile").run([corpus.projects[0]])

    assert result.projects[0].status == "checkout-failed"
    assert result.projects[0].error == "unknown ref"


def test_corpus_forwards_explicit_agent_configuration_to_child(
    tmp_path: Path, monkeypatch: Any
) -> None:
    corpus = load_corpus(_corpus(tmp_path / "corpus.yaml"))
    commands = []
    monkeypatch.setattr(
        CorpusRunner,
        "_execute",
        staticmethod(lambda command, log, **kwargs: (commands.append(command) or 0, None)),
    )
    monkeypatch.setattr(CorpusRunner, "_revision", staticmethod(lambda repository: "abc"))
    monkeypatch.setattr(CorpusRunner, "_profile_state", lambda *args: (1, True))
    CorpusRunner(
        corpus,
        tmp_path / "out",
        profile="test",
        repair_backend="pi",
        coding_agent_model="local/model",
        coding_agent_provider="custom",
        pi_executable="/custom/pi",
        ai_patching=False,
    ).run([corpus.projects[1]])
    command = commands[-1]
    for flag, value in [
        ("--repair-backend", "pi"),
        ("--coding-agent-model", "local/model"),
        ("--coding-agent-provider", "custom"),
        ("--repair-mode", "disabled"),
    ]:
        assert command[command.index(flag) + 1] == value
