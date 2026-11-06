# Additional Coding Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add OpenCode, Codex CLI, and Claude Code repair backends, with managed permissions by default and an inherit option.

**Architecture:** Extract pi's transactional CLI execution into a shared adapter. Keep command construction per backend, reuse Refinery verification and process ownership, and persist all invocation policy for safe resume.

**Tech Stack:** Python, Pydantic settings/models, Typer CLI, pytest executable stubs, existing Git snapshot/process guards.

**Spec:** `docs/superpowers/specs/2026-11-06-additional-coding-agents-design.md`

## Global Constraints

- Agents must already be installed, authenticated, and configured; no SDK/server dependency or installer.
- Preserve internal/pi behavior and deterministic-first repair budgets; external agents cannot enforce strict-local policy.
- Permission setting: `--coding-agent-permissions managed|inherit`, environment `REFINERY_CODING_AGENT_PERMISSIONS`; new invocations default managed, old manifests default inherit.
- No blanket permission/sandbox bypass; retain POSIX process ownership, snapshot rollback, bounded output, timeouts, and independent verification.
- No real authenticated agent/model calls without a separate explicit request.
- Preserve unrelated monitor documents and editor/GitHub files in the main checkout.

## Review Focus

- Ambient provider variables from a different selected backend must produce a clear error, not silently change the provider (Task 2).
- Old completed/interrupted manifests without new fields must retain historical permissions and resume without a false policy mismatch (Task 3).
- Explicit inherit must omit every Refinery permission/approval/sandbox override while retaining non-interactive invocation (Tasks 1/2).
- Large source diagnostics must travel via stdin or a cleaned temporary prompt attachment, never shell interpolation/process arguments (Task 2).
- Agent success with no edits, staged/committed edits, lingering children, or rejected candidate must preserve the existing transactional contract (Tasks 1/2).

### Task 1: Share transactional CLI repair execution without changing pi

**Files:** Create `src/refinery_ai/agents/cli_repair.py`; modify `src/refinery_ai/agents/pi_repair.py`; test `tests/test_pi_repair.py`, `tests/test_migration_runner.py`.

**Interfaces:** Consume `RepairSnapshot.capture(Path)` and existing guarded `spawn_owned`. Produce shared `CliRepairAgent(settings: Settings)` implementing `repair(Path, RecipeRecommendation, VerificationResult) -> VerificationResult`, `accept_last() -> None`, and `rollback_last() -> None`; subclasses supply `command() -> list[str]` and a backend label.

- [x] Add characterization tests asserting both accept and rollback after pi candidates, original exact pi arguments, and no permission flags in either mode (pi has no distinct permission flag). Existing Git mutation, nonzero exit, timeout, output limit, and cancellation tests must keep their behavior.
- [x] Run `PYTHONPATH=src .venv/bin/pytest tests/test_pi_repair.py tests/test_migration_runner.py --no-cov -q`; confirm baseline coverage and add a failing shared-interface test before extraction.
- [x] Move prompt construction, bounded subprocess I/O, termination cleanup, snapshot acceptance/rollback, and candidate checks into `cli_repair.py`. Generalize diagnostics using the selected backend label. Keep `PiRepairAgent` import and constructor compatibility; inspect tests for imports/monkeypatches of moved private helpers before changing them.
- [x] Run the same focused tests; expect PASS. Commit `refactor: share transactional coding agent repair execution`.

### Task 2: Add three command adapters and permission/model configuration

**Files:** Create `src/refinery_ai/agents/coding_agents.py`, `tests/test_coding_agents.py`; modify `src/refinery_ai/models.py`, `src/refinery_ai/config.py`, `src/refinery_ai/agents/repair_backend.py`, `tests/test_repair_backend.py`.

**Interfaces:** Produce `CodingAgentPermissions(StrEnum)` (`managed`, `inherit`); new `RepairBackend` values `opencode`, `codex`, `claude`; settings `opencode_executable="opencode"`, `codex_executable="codex"`, `claude_executable="claude"`, `coding_agent_permissions=MANAGED`. Produce `OpenCodeRepairAgent`, `CodexRepairAgent`, `ClaudeRepairAgent`, each extending Task 1's shared interface.

- [x] Add parametrized executable-stub tests for backend selection, exact command arguments, working directory, stdin prompt, optional model, managed/inherit flags, source acceptance/rollback, nonzero exit, unchanged source, Git index/HEAD mutation, missing executable, timeout, bounded output, and child cleanup. Add settings tests for environment values, invalid permission values, and blank executable/model/provider.
- [x] Assert managed commands: OpenCode `run --auto`; Codex global `--ask-for-approval never` followed by `exec --ephemeral --sandbox workspace-write -`; Claude `--print --no-session-persistence --permission-mode dontAsk --allowedTools Read,Edit,Write,Bash,Glob,Grep`. Inherit omits only permission/approval/sandbox overrides. Optional `--model` is passed as its own argument.
- [x] Assert OpenCode provider+model combines to `provider/model`, model-only native identifiers remain intact, and conflicting qualified provider/model or provider-without-model is rejected. Pi retains its existing two flags. Codex/Claude reject a non-null `coding_agent_provider` before source changes; their native provider settings are inherited.
- [x] Run `PYTHONPATH=src .venv/bin/pytest tests/test_coding_agents.py tests/test_repair_backend.py --no-cov -q`; expect RED for missing adapters/settings.
- [x] Inspect official OpenCode CLI implementation for piped stdin handling. Use it directly if supported; otherwise add a shared prompt-input hook for a private temporary attachment with cleanup on all exits and a static instruction argument. Record the resolved command/input contract in the spec before shipping.
- [x] Implement adapters, nonblank validation, and backend factory selection. `validate_repair_policy(Settings) -> None` rejects strict-local for every external backend and unsupported explicit provider combinations. `build_repair_stages(Settings)` constructs only the selected enabled model adapter; disabled mode never launches/locates an executable.
- [x] Run new tests plus pi tests; expect PASS. Commit `feat: add opencode codex and claude repair adapters`.

### Task 3: Wire CLI, corpus transport, and version-compatible resume policy

**Files:** Modify `src/refinery_ai/cli.py`, `src/refinery_ai/corpus_run.py`, `src/refinery_ai/migrations/execution_state.py`, `src/refinery_ai/agents/repair_backend.py`; tests `tests/test_repair_backend.py`, `tests/test_corpus_run.py`, `tests/test_migration_resume_cli.py`, `tests/test_migration_run_store.py`.

**Interfaces:** `migration_policy(Settings) -> MigrationPolicy` includes permission enum and new executable paths. `MigrationPolicy.coding_agent_permissions` defaults inherit when absent in persisted JSON; Settings still defaults managed. Add new executable/permission options to `_configure_repair` and all three public commands (`migrate`, `run`, `run-corpus`). Extend `CorpusRunner` constructor/settings serialization with the same values.

- [x] Add failing tests for all commands' model/backend/executable/permission flags and environment precedence; corpus child command forwarding; invalid combinations rejected before repository artifacts; no subprocess generation when AI patching is disabled.
- [x] Add old-manifest tests removing the new permission/executable keys, verifying loaded policy inherit and resumed settings inherit. A fresh policy must record managed. Explicit CLI/environment conflicts must fail; omitted defaults must inherit the manifest. Preserve public result schemas.
- [x] Run `PYTHONPATH=src .venv/bin/pytest tests/test_repair_backend.py tests/test_corpus_run.py tests/test_migration_resume_cli.py tests/test_migration_run_store.py --no-cov -q`; expect RED.
- [x] Implement options and environment transport without shell strings. Add all recorded fields to resume settings reconciliation; preserve explicit field-source detection and run/profile behavior. Existing pi executable flag remains accepted.
- [x] Run focused tests plus `tests/test_profile_run.py`; expect PASS. Commit `feat: expose coding agent permissions and preserve resume policy`.

### Task 4: Document, review, and verify complete integration

**Files:** Modify `README.md`, `docs/migration-execution.md`; add `docs/additional-coding-agents-completion.md`.

**Interfaces:** User documentation shows backend selection, preinstalled/configured rule, model formats, executable options, managed versus inherit, agent-specific restrictions, and old-manifest semantics.

- [x] Document examples for `--repair-backend opencode|codex|claude` with configured-provider repair, and `--coding-agent-permissions inherit`. Explain managed permissions are agent-specific and Codex workspace-write may restrict dependency downloads; pi's behavior is unchanged.
- [x] Run full `PYTHONPATH=src OPENAI_API_KEY=test-only-placeholder .venv/bin/pytest -q`, `.venv/bin/mypy`, changed-file Ruff lint/format, and `git diff --check`. Check help for migrate/run/run-corpus. Reproduce pre-existing global style failures separately; do not format unrelated files.
- [x] Obtain one independent whole-branch review per executing-plans, focusing on transactional cleanup, permission opt-out, provider mapping, and old-run resume compatibility. Reproduce important findings with failing tests, fix, and rerun affected/full checks.
- [x] Record exact verification, resolved OpenCode input behavior, decisions, and lack of live model smoke tests. Commit documentation and review fixes; preserve feature worktree for integration choice.

## Execution method

Preserve the native/direct implementation method used earlier in this chat: the
primary agent implements tasks sequentially, followed by one independent final
review. Create an isolated managed worktree at execution time. Existing environment
may use the main checkout's virtualenv with `PYTHONPATH=src` from that worktree.

## Completion

All four task areas are implemented and independently reviewed. See
`docs/additional-coding-agents-completion.md` for actual validation, review fixes,
implementation decisions and external integration limits. No live model calls.
