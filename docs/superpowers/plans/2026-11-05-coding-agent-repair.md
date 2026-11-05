# Selectable Coding Agent Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select internal scoped repair or an installed pi coding agent without changing Refinery's verification and publishing guarantees.

**Architecture:** A shared factory selects a transactional repairer. Pi runs once per attempt in the migration worktree; a source snapshot restores unsuccessful candidates. Existing migration orchestration owns verification and acceptance.

**Tech Stack:** Python 3.12+, Pydantic settings, Typer, subprocess, Git, pytest; no new production dependencies.

**Spec:** `docs/superpowers/specs/2026-11-05-coding-agent-repair-design.md`

## Global Constraints

- `internal` is the default; `pi` must already be installed and configured.
- Disabled repair starts no model or external agent; pi with strict local policy is rejected explicitly.
- Separate optional agent provider/model overrides from the existing internal repair model.
- One fresh process per attempt, no shell, no saved session, prompt on stdin.
- Retain deterministic-first ordering, existing attempt budgets and improvement scoring.
- Restore partial edits before returning failure; stop descendant processes before rollback.
- Worktrees are not a filesystem sandbox; never claim restricted agent command access.
- Do not modify unrelated untracked editor or GitHub configuration.

## Review Focus

- A failed attempt after an earlier accepted repair restores that accepted state, not HEAD (Task 1).
- Symlinks pointing outside the worktree must never cause snapshot reads/writes outside it (Task 1).
- An agent child process must stop writing before rollback after timeout/cancellation (Task 2).
- A child launched from a different directory must receive explicit corpus configuration (Task 3).
- Empty overrides and model names containing slashes or punctuation must not become malformed shell arguments (Tasks 2–3).

### Task 1: Source transaction for direct-edit agents

**Files:** Create `src/refinery_ai/agents/repair_snapshot.py`, `tests/test_repair_snapshot.py`.

**Interfaces:** `RepairSnapshot.capture(repository: Path) -> RepairSnapshot`, `restore() -> None`, `changed() -> bool`, `git_state_changed() -> bool`, `close() -> None`.

- [ ] Write failing tests in real temporary Git repositories. Capture a dirty recipe edit, a staged file, an untracked file, an executable and a symlink; mutate/delete these and add a binary file. Assert restore reproduces bytes, modes, link targets, HEAD and index and removes the new file.
- [ ] Add tests for a second capture after an accepted repair, outside-target symlinks, a file replaced by a directory, a newly added directory tree, ignored source files, and generated output exclusions. Assert outside targets remain untouched and generated-only edits do not count as repair changes.
- [ ] Run `.venv/bin/pytest tests/test_repair_snapshot.py --no-cov -q`; confirm failures identify the missing transaction implementation.
- [ ] Implement the interface using temporary disk snapshots outside the worktree, lstat-based traversal that does not follow links, and Git state capture. Exclude `.git` and existing migration-generated directory names. Preserve pre-existing untracked source files. Capture index existence/bytes and HEAD state, and fail closed if capture cannot complete.
- [ ] Run the snapshot tests; expect all to pass. Commit the focused transaction change.

### Task 2: One-shot pi repairer

**Files:** Create `src/refinery_ai/agents/pi_repair.py`, `tests/test_pi_repair.py`; modify `src/refinery_ai/config.py` for executable/provider/model settings.

**Interfaces:** `PiRepairAgent(settings: Settings)` implements `repair(repository: Path, step: RecipeRecommendation, failure: VerificationResult) -> VerificationResult`, `accept_last() -> None`, `rollback_last() -> None`. Consumes Task 1's snapshot. Settings expose `pi_executable: str = "pi"`, `coding_agent_model: str | None = None`, `coding_agent_provider: str | None = None`.

- [ ] Write failing fake-executable tests asserting cwd is the supplied worktree, stdin includes recipe and failure, arguments include `--print` and `--no-session`, and provider/model flags appear only for explicit overrides. Assert names containing punctuation arrive as a single argument. Blank overrides must be rejected with a useful diagnostic.
- [ ] Add tests for missing executable, nonzero exit after partial edits, successful no-op, generated-only changes, changed HEAD/index, large stdout/stderr, accepted candidates, explicit rollback and repeated attempts. Assert failures restore source state and successful changes remain pending until accepted.
- [ ] Add timeout and cancellation tests with an executable spawning a delayed writer. Assert the process group is terminated before source restoration and no later write appears. Exercise normal termination and a child ignoring graceful termination.
- [ ] Run `.venv/bin/pytest tests/test_pi_repair.py --no-cov -q`; confirm expected failures.
- [ ] Implement direct subprocess invocation with a new process group, bounded streamed output capture and the existing command timeout. Send the prompt through stdin; inherit pi authentication/configuration, without passing secrets as arguments. Do not automatically approve project-local extensions. Treat successful exit plus a source diff as a candidate, never as independent build verification. Return actual elapsed duration and bounded diagnostics. On BaseException cleanup, terminate descendants and restore before propagating cancellation.
- [ ] Run pi and snapshot tests; expect all to pass. Commit adapter and configuration.

### Task 3: Backend selection and workflow propagation

**Files:** Modify `src/refinery_ai/models.py`, `src/refinery_ai/config.py`, `src/refinery_ai/cli.py`, `src/refinery_ai/corpus_run.py`; create `src/refinery_ai/agents/repair_backend.py`, `tests/test_repair_backend.py`; extend `tests/test_migration_runner.py`, `tests/test_profile_run.py`, `tests/test_corpus_run.py`.

**Interfaces:** `RepairBackend(StrEnum)` has `INTERNAL = "internal"`, `PI = "pi"`; `Settings.repair_backend: RepairBackend = RepairBackend.INTERNAL`. Factory `build_repair_stages(settings: Settings) -> tuple[MigrationRepairStage, ...]` preserves deterministic-first ordering and selects the model repairer only when enabled. CorpusRunner receives optional backend/model/provider keyword arguments and forwards explicit CLI flags to child profile commands.

- [ ] Write tests for default internal, enabled pi, disabled pi (no construction/process), strict-local pi rejection, internal-local validation, invalid backend and missing overrides. Assert the internal repair model is never forwarded as pi's default.
- [ ] Add CLI tests for `migrate`, profile `run`, and `run-corpus` with `--repair-backend`, `--coding-agent-model`, `--coding-agent-provider`. Assert CLI precedence over environment and unchanged defaults. Assert blank overrides fail before migration side effects.
- [ ] Add corpus tests asserting child arguments carry the selected backend/model/provider even when clones use a different cwd; disabled AI patching still forwards disabled policy and never launches pi.
- [ ] Add runner integration tests with a fake pi executable: a green or improved candidate is accepted; a non-improving candidate reverts; a failed invocation cannot leak partial edits into the next attempt; only green steps checkpoint. Assert earlier recipe changes survive.
- [ ] Run the affected tests and confirm new assertions fail before integration changes.
- [ ] Implement the enum, shared stage factory and CLI configuration application. Replace both existing duplicated stage constructors. Expose the three options consistently on migration/profile/corpus commands and propagate settings explicitly into corpus subprocess arguments. Use backend-specific stage labels while retaining `uses_model=True` and existing attempt budgets. Return actionable CLI errors for unsupported policy combinations without falling back.
- [ ] Run `.venv/bin/pytest tests/test_repair_backend.py tests/test_pi_repair.py tests/test_repair_snapshot.py tests/test_migration_runner.py tests/test_profile_run.py tests/test_corpus_run.py tests/test_ai_repair.py tests/test_repair.py --no-cov -q`; expect all to pass. Commit workflow integration.

### Task 4: Documentation and complete verification

**Files:** Modify `README.md`, `.env.example`; update this plan's checkboxes after verification.

**Interfaces:** Document public configuration introduced by Tasks 2–3; no additional runtime interface.

- [ ] Add configuration examples for default internal, pi using its configured model, explicit pi provider/model overrides, custom executable path, and local models through pi with configured-provider policy. State install/configuration prerequisites and that Refinery does not install or authenticate agents.
- [ ] Explain full-worktree disclosure, command access, timeout/rollback behavior, independent Refinery verification and strict-local policy incompatibility. Keep existing internal setup working and correct the stale Pydantic AI repair wording in `.env.example`.
- [ ] Run `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy`, and `.venv/bin/pytest`. Resolve failures attributable to the change; record any pre-existing or environment-only limitations accurately.
- [ ] Inspect help output for the three commands and compare README examples with actual option names. Inspect the diff for unintended files and credentials. Obtain a whole-change review and resolve actionable findings, then rerun only affected checks if changes occur.
- [ ] Commit documentation and report tests run, results, and whether any live pi/model smoke test was performed. Do not imply fake-agent tests validate a real model's repair quality.
