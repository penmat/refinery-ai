from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from refinery_ai.cli import app
from refinery_ai.corpus_run import CorpusRunner, load_corpus, load_failed_project_ids


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
    monkeypatch.setattr(CorpusRunner, "_profile_state", lambda self, repository: (2, True))
    result = CorpusRunner(corpus, tmp_path / "run", profile="profile").run([corpus.projects[0]])

    assert result.projects[0].status == "completed"
    assert result.projects[0].source_revision == "abc123"
    assert result.projects[0].requested_ref == "v2.6.2"
    assert result.projects[0].profile_cycles == 2
    assert result.projects[0].tier == "easy"
    assert commands[0][:3] == ["git", "clone", "--quiet"]
    assert commands[1][-2:] == ["--quiet", "v2.6.2"]
    assert "--repair-mode" in commands[2]
    assert commands[2][commands[2].index("--repair-mode") + 1] == "configured-provider"
    assert (tmp_path / "run" / "summary.json").is_file()


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


def test_load_failed_project_ids_accepts_run_directory(tmp_path: Path) -> None:
    run = tmp_path / "prior-run"
    run.mkdir()
    (run / "summary.json").write_text(
        '{"projects": ['
        '{"id": "first", "status": "failed"},'
        '{"id": "second", "status": "completed"},'
        '{"id": "third", "status": "timed-out"}'
        ']}\n'
    )

    assert load_failed_project_ids(run) == {"first", "third"}


def test_run_corpus_reruns_failures_but_still_skips_test_configuration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    corpus_path = _corpus(tmp_path / "corpus.yaml")
    prior_summary = tmp_path / "prior-summary.json"
    prior_summary.write_text(
        '{"projects": ['
        '{"id": "first", "status": "failed"},'
        '{"id": "second", "status": "failed"}'
        ']}\n'
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
