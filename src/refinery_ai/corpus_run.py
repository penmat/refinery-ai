"""Run a migration profile against a repository corpus."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, HttpUrl


class CorpusProject(BaseModel):
    id: str
    name: str
    repo_url: HttpUrl
    tier: str | None = None
    category: str | None = None
    notes: str | None = None
    ref: str | None = None
    test_configuration_needed: bool = False
    baseline_test: bool = False
    baseline_migrations: list[str] = Field(default_factory=list)


class CorpusDefinition(BaseModel):
    schema_version: int = 1
    corpus: dict[str, Any] = Field(default_factory=dict)
    projects: list[CorpusProject]


class CorpusProjectResult(BaseModel):
    id: str
    name: str
    repo_url: str
    tier: str | None = None
    category: str | None = None
    test_configuration_needed: bool = False
    baseline_test: bool = False
    status: str
    ai_patching: bool
    elapsed_seconds: float
    started_at: datetime
    finished_at: datetime
    source_revision: str | None = None
    requested_ref: str | None = None
    result_revision: str | None = None
    repository: str
    log: str
    exit_code: int | None = None
    error: str | None = None
    profile_cycles: int | None = None
    profile_completed: bool | None = None


class CorpusRunResult(BaseModel):
    corpus_id: str
    profile: str
    ai_patching: bool
    jobs: int = 1
    started_at: datetime
    finished_at: datetime | None = None
    projects: list[CorpusProjectResult] = Field(default_factory=list)


def load_corpus(path: Path) -> CorpusDefinition:
    """Load and validate a corpus YAML document."""

    document = yaml.safe_load(path.expanduser().resolve().read_text())
    return CorpusDefinition.model_validate(document)


def load_failed_project_ids(path: Path) -> frozenset[str]:
    """Load unsuccessful project IDs from a prior corpus summary or run directory."""

    summary = path.expanduser().resolve()
    if summary.is_dir():
        summary = summary / "summary.json"
    try:
        document = json.loads(summary.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read prior corpus summary {summary}: {error}") from error
    projects = document.get("projects") if isinstance(document, dict) else None
    if not isinstance(projects, list):
        raise ValueError(f"Prior corpus summary has no projects list: {summary}")
    failed: set[str] = set()
    for project in projects:
        if not isinstance(project, dict):
            continue
        project_id = project.get("id")
        status = project.get("status")
        if isinstance(project_id, str) and status != "completed":
            failed.add(project_id)
    return frozenset(failed)


def load_attempted_project_ids(path: Path) -> frozenset[str]:
    """Load every project ID recorded beneath a prior-run history path."""

    history = path.expanduser().resolve()
    if history.is_file():
        summaries = [history]
    elif (history / "summary.json").is_file():
        summaries = [history / "summary.json"]
    elif history.is_dir():
        summaries = sorted(history.glob("*/summary.json"))
    else:
        raise ValueError(f"Corpus run history does not exist: {history}")
    if not summaries:
        return frozenset()
    attempted: set[str] = set()
    for summary in summaries:
        try:
            document = json.loads(summary.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Unable to read prior corpus summary {summary}: {error}") from error
        projects = document.get("projects") if isinstance(document, dict) else None
        if not isinstance(projects, list):
            raise ValueError(f"Prior corpus summary has no projects list: {summary}")
        attempted.update(
            project["id"]
            for project in projects
            if isinstance(project, dict) and isinstance(project.get("id"), str)
        )
    return frozenset(attempted)


class CorpusRunner:
    """Clone each selected project and invoke the isolated profile-run command."""

    def __init__(
        self,
        corpus: CorpusDefinition,
        output_directory: Path,
        *,
        profile: str,
        ai_patching: bool = True,
        max_cycles: int = 20,
        timeout_seconds: int = 14_400,
        jobs: int = 1,
        progress: Any = None,
    ) -> None:
        if jobs < 1:
            raise ValueError("jobs must be at least 1")
        self.corpus = corpus
        self.output_directory = output_directory.expanduser().resolve()
        self.profile = profile
        self.ai_patching = ai_patching
        self.max_cycles = max_cycles
        self.timeout_seconds = timeout_seconds
        self.jobs = jobs
        self.progress = progress or (lambda _message: None)
        self._cancelled = threading.Event()
        self._process_lock = threading.Lock()
        self._active_processes: set[subprocess.Popen[str]] = set()

    def run(self, projects: list[CorpusProject]) -> CorpusRunResult:
        started_at = datetime.now(UTC)
        result = CorpusRunResult(
            corpus_id=str(self.corpus.corpus.get("id", "unnamed-corpus")),
            profile=self.profile,
            ai_patching=self.ai_patching,
            jobs=self.jobs,
            started_at=started_at,
        )
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self._save(result)
        if self.jobs == 1:
            self._run_sequential(projects, result)
        else:
            self._run_parallel(projects, result)
        result.finished_at = datetime.now(UTC)
        self._save(result)
        return result

    def _run_sequential(self, projects: list[CorpusProject], result: CorpusRunResult) -> None:
        for index, project in enumerate(projects, start=1):
            self.progress(f"[{index}/{len(projects)}] {project.id}: cloning and converting ...")
            project_result = self._run_project(project)
            result.projects.append(project_result)
            self._save(result)
            self.progress(
                f"[{index}/{len(projects)}] {project.id}: {project_result.status} "
                f"({project_result.elapsed_seconds:.1f}s)"
            )

    def _run_parallel(self, projects: list[CorpusProject], result: CorpusRunResult) -> None:
        completed: dict[int, CorpusProjectResult] = {}
        next_index = 0
        futures: dict[Future[CorpusProjectResult], tuple[int, CorpusProject]] = {}
        executor = ThreadPoolExecutor(max_workers=self.jobs, thread_name_prefix="corpus")
        try:
            while next_index < len(projects) and len(futures) < self.jobs:
                next_index = self._submit_project(executor, futures, projects, next_index)
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    index, project = futures.pop(future)
                    project_result = future.result()
                    completed[index] = project_result
                    result.projects = [completed[key] for key in sorted(completed)]
                    self._save(result)
                    self.progress(
                        f"[{index + 1}/{len(projects)}] {project.id}: "
                        f"{project_result.status} ({project_result.elapsed_seconds:.1f}s)"
                    )
                    if next_index < len(projects):
                        next_index = self._submit_project(executor, futures, projects, next_index)
        except KeyboardInterrupt:
            self._cancelled.set()
            self._terminate_active_processes()
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    def _submit_project(
        self,
        executor: ThreadPoolExecutor,
        futures: dict[Future[CorpusProjectResult], tuple[int, CorpusProject]],
        projects: list[CorpusProject],
        index: int,
    ) -> int:
        project = projects[index]
        self.progress(f"[{index + 1}/{len(projects)}] {project.id}: cloning and converting ...")
        futures[executor.submit(self._run_project, project)] = (index, project)
        return index + 1

    def _run_project(self, project: CorpusProject) -> CorpusProjectResult:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        repository = self.output_directory / "repositories" / project.id
        log_path = self.output_directory / "logs" / f"{project.id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        repository.parent.mkdir(parents=True, exist_ok=True)
        status = "failed"
        error: str | None = None
        exit_code: int | None = None
        source_revision: str | None = None
        result_revision: str | None = None
        profile_cycles: int | None = None
        profile_completed: bool | None = None
        clone_completed = False

        with log_path.open("w") as log:
            self._log(log, f"Project: {project.id}\nRepository: {project.repo_url}\n")
            clone = ["git", "clone", "--quiet", str(project.repo_url), str(repository)]
            exit_code, error = self._execute(clone, log, timeout=600)
            clone_completed = exit_code == 0
            if exit_code == 0 and project.ref:
                exit_code, error = self._execute(
                    ["git", "-C", str(repository), "checkout", "--quiet", project.ref],
                    log,
                    timeout=120,
                )
            if exit_code == 0:
                source_revision = self._revision(repository)
                command = [
                    sys.executable,
                    "-m",
                    "refinery_ai.cli",
                    "run",
                    "--repository",
                    str(repository),
                    "--profile",
                    self.profile,
                    "--apply",
                    "--max-cycles",
                    str(self.max_cycles),
                    "--repair-mode",
                    "configured-provider" if self.ai_patching else "disabled",
                ]
                exit_code, error = self._execute(command, log, timeout=self.timeout_seconds)
                result_revision = self._revision(repository)
                profile_cycles, profile_completed = self._profile_state(repository)
                status = (
                    "completed"
                    if exit_code == 0
                    else "timed-out"
                    if exit_code is None
                    else "failed"
                )
            else:
                status = "checkout-failed" if clone_completed else "clone-failed"

        finished_at = datetime.now(UTC)
        return CorpusProjectResult(
            id=project.id,
            name=project.name,
            repo_url=str(project.repo_url),
            tier=project.tier,
            category=project.category,
            test_configuration_needed=project.test_configuration_needed,
            baseline_test=project.baseline_test,
            status=status,
            ai_patching=self.ai_patching,
            elapsed_seconds=round(time.monotonic() - started, 3),
            started_at=started_at,
            finished_at=finished_at,
            source_revision=source_revision,
            requested_ref=project.ref,
            result_revision=result_revision,
            repository=str(repository),
            log=str(log_path),
            exit_code=exit_code,
            error=error,
            profile_cycles=profile_cycles,
            profile_completed=profile_completed,
        )

    def _execute(
        self, command: list[str], log: TextIO, *, timeout: int
    ) -> tuple[int | None, str | None]:
        CorpusRunner._log(log, f"\n$ {' '.join(command)}\n")
        if self._cancelled.is_set():
            return 130, "Corpus run cancelled"
        try:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, text=True)
            with self._process_lock:
                self._active_processes.add(process)
            try:
                return process.wait(timeout=timeout), None
            finally:
                with self._process_lock:
                    self._active_processes.discard(process)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            message = f"Command timed out after {timeout} seconds"
            CorpusRunner._log(log, f"\n{message}\n")
            return None, message
        except OSError as exc:
            CorpusRunner._log(log, f"\nUnable to start command: {exc}\n")
            return 127, str(exc)

    def _terminate_active_processes(self) -> None:
        with self._process_lock:
            processes = tuple(self._active_processes)
        for process in processes:
            if process.poll() is None:
                process.terminate()

    @staticmethod
    def _revision(repository: Path) -> str | None:
        completed = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    def _profile_state(self, repository: Path) -> tuple[int | None, bool | None]:
        path = repository / ".refinery" / "profile-runs" / f"{self.profile}.json"
        try:
            state = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None, None
        cycles = state.get("cycles")
        completed = state.get("completed")
        return (
            cycles if isinstance(cycles, int) else None,
            completed if isinstance(completed, bool) else None,
        )

    @staticmethod
    def _log(log: TextIO, message: str) -> None:
        log.write(message)
        log.flush()

    def _save(self, result: CorpusRunResult) -> None:
        path = self.output_directory / "summary.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(result.model_dump_json(indent=2) + "\n")
        temporary.replace(path)
