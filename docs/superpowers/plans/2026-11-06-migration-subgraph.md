# Migration Subgraph and Safe Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute migrations through explicit LangGraph nodes, report structured events, and safely resume from the last verified recipe.

**Architecture:** Keep `MigrationRunner` as the public facade and move sequencing into one compiled graph. Runtime dependencies stay in an invocation context; durable state, verified Git receipts, and process ownership are reconciled before recovery. Lifecycle events feed both a local journal and LangGraph custom streaming.

**Tech Stack:** Python 3.12+, installed LangGraph 1.2.11, existing SQLite checkpointer, Pydantic, Git, POSIX locks/process groups, pytest. No new production dependency.

**Spec:** `docs/superpowers/specs/2026-11-06-migration-subgraph-design.md`

## Global Constraints

- Preserve `MigrationRunner.run(...) -> MigrationRun`, serialized public results, current progress text, dry-run behavior, JDK selection, repair budgets, and verified publication policy.
- Preserve completed recipes; restart only the interrupted recipe. Never resume inside a pi session or reuse an in-memory repair snapshot after a crash.
- Every repair attempt is a separate graph visit. No automatic retry of non-idempotent nodes.
- New apply runs have immutable pinned plans/policies and stable run IDs; preview and legacy runs are not resumable.
- Git effects and graph checkpoints are not one transaction: reconcile verified receipts before continuing.
- Refuse unsafe ownership, dirty/moved source, changed plans/policies, corrupt receipts, unknown schema versions, and explicit terminal failures.
- Events exclude credentials, prompts, source contents, raw model responses, and full build output.
- Apply journals live at `<repository>/<state_directory>/migration-events/<run_id>.jsonl`; preview produces no journal by default.
- Keep existing profile checkpointing and injected migration callbacks; real child invocations recover their bound run rather than duplicate it.
- Safe resume initially requires POSIX support. Preserve existing non-resume operation elsewhere and reject unsupported resume explicitly.
- Leave unrelated `.github/`, `.idea/`, and `.vscode/` content untouched.

## Review Focus

- A child closes inherited file descriptors and outlives the worker: resume must still refuse while its recorded process group is alive (Task 1).
- A green no-op recipe retains the predecessor revision: a receipt, not a changed SHA, proves it completed (Tasks 2 and 5).
- A completion receipt exists but the graph checkpoint is stale: import the receipt without executing that recipe again (Task 5).
- Source was fast-forwarded before result persistence or the parent checkpoint: return/reconcile that publication without merging again (Tasks 5 and 6).
- Reporter failure occurs between repair and evaluation: restore the candidate, preserve the original error, and leave earlier checkpoints intact (Tasks 3 and 4).

## File map and shared interfaces

- `migrations/process_safety.py`, `process_guard.py`: invocation leases, persistent child-group registration, and guarded process launch.
- `migrations/execution_state.py`, `run_store.py`: typed manifests/policies/receipts/recovery points and atomic artifact persistence.
- `migrations/execution_context.py`, `baseline.py`, `step_execution.py`, `repair_execution.py`: focused effectful operations used by graph nodes.
- `workflows/migration.py`: state schema, compiled nodes/edges, recursion bound, synchronous durable invocation.
- `migrations/events.py`: typed safe events, journal replay/append, isolated observers, custom stream forwarding.
- `migrations/recovery.py`: validate and reconcile interrupted runs before graph re-entry.
- Existing runner, worktree manager, verification, pi adapter, internal dependency validation, CLI, and profile workflow: integrate these services while preserving their existing public seams.
- New tests match these boundaries; fault-injection utilities live under `tests/`, never as production testing hooks.

Shared models in `execution_state.py` are versioned Pydantic values, with no live services or credentials:

- `MigrationPolicy`: repair mode/backend/model/provider/executable, stage descriptors/budgets, command timeout, and output limit. External model defaults remain explicitly unset when inherited.
- `RunManifest`: schema version 1, canonical repository/state root, run/plan identity, pinned `PersistedMigrationPlan` and canonical digest, starting revision, policy, publication intent, and checkpoint-store reference.
- `BaselineReceipt`: verified/reused baseline result, exact revision and JDK.
- `StepReceipt`: step index/recipe digest, predecessor/checkpoint revisions, generation, JDK, successful verification, and compatible `StepResult`.
- `PublicationIntent`: expected source revision, verified target revision, and publication status.
- `RecoveryPoint`: verified prefix/results, baseline receipt, next step, current revision/JDK, incremented generation, and optional completed `MigrationRun`.

### Task 1: Ownership that covers external processes

**Files:** Create `src/refinery_ai/migrations/process_safety.py`, `src/refinery_ai/migrations/process_guard.py`, `tests/test_migration_process_safety.py`. Modify `src/refinery_ai/verification.py`, `src/refinery_ai/agents/pi_repair.py`, and the migration-time dependency subprocess in `src/refinery_ai/agents/ai_repair.py`.

**Interfaces:** `MigrationLease(repository: Path, state_directory: Path, run_id: str)` is a context manager. `assert_children_stopped() -> None` checks recorded process groups without killing unidentified/reused processes. `spawn_owned(command: list[str], *, cwd: Path, stdin: BinaryIO | None, env: dict[str, str] | None) -> Popen[bytes]` captures stdout/stderr; `run_owned(command: list[str], *, cwd: Path, timeout: int, env: dict[str, str] | None) -> CompletedProcess[str]` offers the blocking counterpart. An invocation `ContextVar` binds the lease; unbound use retains existing subprocess behavior.

- [ ] Write real-process tests `test_second_owner_is_refused`, `test_dead_parent_with_live_child_blocks_recovery`, `test_child_closing_lease_fd_still_blocks_recovery`, and `test_stale_group_record_never_kills_reused_process`. Assert acquisition fails without source/worktree changes while a competing owner/group lives, then succeeds after the owned group ends. Test cleanup includes all spawned test processes even if an assertion fails.
- [ ] Add `test_worker_dies_during_launch_handshake`: kill the worker before command execution is acknowledged; assert resume cannot slip past a live bootstrap child. Add POSIX-unavailable tests: resume refuses explicitly, ordinary unbound verification still works.
- [ ] Run `uv run pytest tests/test_migration_process_safety.py --no-cov -q`. Expected: RED for missing ownership/launch behavior.
- [ ] Implement a POSIX advisory invocation lock plus a persistent registry of launch intents and child process groups. Use a bootstrap/acknowledgement boundary so the child retains the lease and records its group before executing the actual source-mutating command. Group liveness remains authoritative if the target CLI closes extra descriptors. Retain records until the group is absent; uncertainty is a refusal, not permission to kill arbitrary PIDs. Use a separate repository publication lock for final source updates.
- [ ] Bind guarded launching into migration-time verification (also covers OpenRewrite, which delegates to `verify`), pi, and internal dependency validation. Retain original command arguments in diagnostics/results; do not expose bootstrap arguments or credentials. Keep existing timeout/cancellation cleanup and stop descendants before returning failure. Builds or agents that deliberately escape their process group are not newly sandboxed; do not claim containment of arbitrary external daemons.
- [ ] Run `uv run pytest tests/test_migration_process_safety.py tests/test_verification.py tests/test_openrewrite.py tests/test_pi_repair.py tests/test_ai_repair.py --no-cov -q`. Expected: GREEN. Commit `feat: track migration ownership across child processes`.

### Task 2: Durable run identity and verified receipts

**Files:** Create `src/refinery_ai/migrations/execution_state.py`, `src/refinery_ai/migrations/run_store.py`, `tests/test_migration_run_store.py`; modify `src/refinery_ai/migrations/worktrees.py`, extend `tests/test_migration_worktrees.py`.

**Interfaces:** `MigrationRunStore(repository: Path, state_directory: Path, run_id: str)` exposes `create(manifest: RunManifest)`, `load_manifest() -> RunManifest`, `save_baseline(receipt: BaselineReceipt)`, `save_step(receipt: StepReceipt)`, `load_steps() -> list[StepReceipt]`, `save_publication(intent: PublicationIntent)`, `load_publication() -> PublicationIntent | None`, `save_result(result: MigrationRun)`, `load_result() -> MigrationRun | None`, and `find_bound_run(plan_id: str) -> str | None`. All writes are atomic and version validated.

- [ ] Write failing store tests pinning schema version `1`, canonical plan digest, same-run repository binding, ordered receipts, secret-free policies, unknown-version rejection, and invalid/path-traversal run IDs. Assert malformed or mismatched data fails closed.
- [ ] Write real-Git tests showing a green changed recipe and a green no-op recipe each get a receipt tied to their exact predecessor, step, JDK, successful verification, and checkpoint ref. Assert a ref/commit without a receipt is not returned as completed. Assert public `StepResult`/`MigrationRun` output contracts remain unchanged.
- [ ] Run `uv run pytest tests/test_migration_run_store.py tests/test_migration_worktrees.py --no-cov -q`. Expected: RED for missing durable models/receipt behavior.
- [ ] Implement the shared models and store. Keep immutable provenance in `migration-runs/<run_id>/manifest.json`, compatible runtime metadata in `run.json`, receipts alongside existing step artifacts, and a stored final result. Write temporary files, flush/fsync, atomically replace, and sync the containing directory on supported POSIX systems. Do not persist secrets or runtime dependency objects.
- [ ] Retain `GitWorktreeManager.start` ownership of initial directory creation; persist the full manifest immediately afterward and before baseline/recipe mutation. Add an attach-existing-run path that validates rather than calls `start` again. Use extended versioned `step.json` completion metadata as the receipt, retaining the existing recipe/status/result fields for failure-history consumers. Write that receipt last and atomically, after verification evidence, commit/ref, and diagnostic artifacts. Preserve existing worktree tests.
- [ ] Add explicit generation/attempt identity support for restarted worktrees and archived interrupted artifacts, leaving the first attempt's existing paths compatible. Do not remove or overwrite earlier verified artifacts. Resolve owned paths beneath the configured state root and reject symlink/path escapes.
- [ ] Run the store/worktree tests. Expected: GREEN. Commit `feat: persist migration manifests and verified recipe receipts`.

### Task 3: Replace imperative orchestration with the migration graph

**Files:** Create `src/refinery_ai/workflows/migration.py`, `src/refinery_ai/migrations/execution_context.py`, `baseline.py`, `step_execution.py`, `repair_execution.py`, `tests/test_migration_graph.py`. Modify `src/refinery_ai/migrations/runner.py`; extend `tests/test_migration_runner.py` and existing characterization tests only with additional assertions.

**Interfaces:** `MigrationRequest` (in `execution_state.py`) carries apply/publish/profile/trusted-baseline inputs plus optional resume identity. `MigrationExecutionContext` holds runner dependencies, lease/store/reporting resources, and the currently pending transactional repairer. `build_migration_graph(context: MigrationExecutionContext, *, checkpointer: BaseCheckpointSaver[str] | None = None)` returns the compiled typed graph. `initial_migration_state(plan: PersistedMigrationPlan, request: MigrationRequest, manifest: RunManifest | None, recovery: RecoveryPoint | None = None) -> MigrationGraphState` constructs pure state. `run_migration_graph(runner: MigrationRunner, request: MigrationRequest) -> MigrationRun` is the facade entry point consumed by Task 6.

- [ ] Add failing real-graph tests proving recipe order, preview dispatch, trusted baseline reuse, fallback JDKs, post-upgrade JDK switch, deterministic-first repair selection, separate stage budgets, rejected proposals, accepted intermediate improvements, and stop-on-failure routing. Assert existing calls, progress strings, results, and Git effects rather than updating golden expectations to match a new implementation.
- [ ] Add structural tests for `prepare_run`, `recover_run`, `verify_baseline`, `prepare_step`, `execute_recipe`, `verify_candidate`, `select_repair`, `repair`, `evaluate_repair`, `checkpoint_step`, `record_step_failure`, `publish_result`, `finish_run`. Assert explicit conditional repair/step edges and multiple `repair` visits for retries. Exercise a 40-recipe plan beyond the default recursion allowance.
- [ ] Add tests for unknown execution type/build system, empty plans, recipe-command failure, stage exhaustion, publication of an earlier green checkpoint after a later failure, and pending transactional rollback when execution exits between `repair` and `evaluate_repair`. Preserve RuntimeError/KeyboardInterrupt propagation.
- [ ] Run `uv run pytest tests/test_migration_graph.py tests/test_migration_runner.py tests/test_workflow_characterization.py --no-cov -q`. Expected: RED for graph execution/structure.
- [ ] Extract baseline, recipe, and repair operations into their focused modules. Move failure scoring/display helpers only if needed, re-exporting existing imports from `runner.py`. Remove the imperative execution path from `MigrationRunner.run`; it delegates through a local import as `ProfileRunner.run` does today. Dependencies remain injectable and are not serialized.
- [ ] Implement the graph with typed value-only state, explicit edges, no automatic side-effect retries, synchronous SQLite durability for apply mode, and recursion allowance derived from recipe count and maximum repair visits. Preserve live invocation cleanup outside node state so pending repairs restore even when graph persistence fails. Preview compiles without durable checkpointing or event files.
- [ ] Run `uv run pytest tests/test_migration_graph.py tests/test_migration_runner.py tests/test_baseline.py tests/test_migration_worktrees.py tests/test_workflow_characterization.py tests/test_pi_repair.py --no-cov -q`. Expected: GREEN. Commit `refactor: execute migrations through explicit LangGraph nodes`.

### Task 4: Stable lifecycle events and replayable reporting

**Files:** Create `src/refinery_ai/migrations/events.py`, `tests/test_migration_events.py`; modify graph/context integration and extend `tests/test_migration_graph.py`.

**Interfaces:** `MigrationEvent(BaseModel)` has schema version 1 and the fields in the spec, with lifecycle literals `started|completed|failed|cancelled|interrupted`. `MigrationReporter` owns per-run sequence allocation, journal append/flush/close, and optional `Callable[[MigrationEvent], None]` observers. `read_migration_events(path: Path) -> list[MigrationEvent]` validates replay. `MigrationRunner(..., event_observer: Callable[[MigrationEvent], None] | None = None)` is additive.

- [ ] Write failing event tests for unique event/execution IDs, matched node start/completion, repeated repair visits with attempt context, UTC timestamps, monotonic durations, and domain outcomes distinct from exceptions. A returned red build must route to repair and emit `completed` with a failed-build outcome, not an unexpected-exception event.
- [ ] Test journal sequence/order/schema validation, an incomplete final record, malformed complete records, sensitive data exclusion, preview observers without journal creation, callback failure isolation, and deterministic discovery from the existing result's run ID. Observer errors must not stop verification; journal errors must stop before the next side effect.
- [ ] Reproduce the Review Focus reporter failure after pi applies a candidate and before evaluation: assert original source/earlier checkpoint survives, candidate additions are removed, the original reporting error propagates, and a failure in reporting cleanup cannot replace it. Test a post-publication journal failure never resets the source checkout.
- [ ] Run `uv run pytest tests/test_migration_events.py tests/test_migration_graph.py --no-cov -q`. Expected: RED for missing event behavior.
- [ ] Implement typed graph-boundary reporting and detail events for each fallback baseline JDK. Persist before notifying observers/forwarding custom events; isolate observer errors in diagnostics. Forward only the safe envelope through `get_stream_writer` when graph context exists. Exception/cancellation reporting is secondary to cleanup and the original exception.
- [ ] On recovery, validate/replay the journal, truncate only an incomplete trailing record, continue sequence numbers, close unmatched prior executions as interrupted, and include the new generation in recovery/restart events. Keep detailed output in existing artifacts, not event payloads.
- [ ] Run event, graph, and transactional pi tests. Expected: GREEN. Commit `feat: report migration lifecycle through durable structured events`.

### Task 5: Reconcile and resume at verified recipe boundaries

**Files:** Create `src/refinery_ai/migrations/recovery.py`, `tests/test_migration_recovery.py`, `tests/migration_fault_worker.py`; modify store, worktree manager, and graph entry/publication integration.

**Interfaces:** `recover_migration(store: MigrationRunStore, policy: MigrationPolicy, *, lease: MigrationLease) -> RecoveryPoint` validates evidence before changing anything. `run_migration_graph` uses its result with `initial_migration_state`, then enters `recover_run` and the correct baseline/recipe route. Resume never blindly invokes an arbitrary pending side-effect node with `None` input.

- [ ] Write subprocess crash tests using real temporary Git repositories, fake recipe/build/agent executables, and external test counters. Terminate workers after recipe verification, commit/ref creation, receipt persistence, and graph checkpoint persistence. Assert completed receipts skip recipe execution; a commit without a receipt restarts only that recipe; the new workspace equals the predecessor; successful no-op recipes are retained; JDK state matches the verified prefix. Synchronize kills using test-owned wrapper callbacks/markers, not production fault switches or timing guesses.
- [ ] Add crash tests before publication, after source fast-forward, and before result/finalization persistence. Assert publication intent binds the exact verified target, an already applied target is not merged again, and an unrelated moved HEAD refuses recovery. Test repeated recovery is idempotent and a stored completed run returns its original result.
- [ ] Add refusal tests for live owner/orphan groups, simultaneous resumes, dirty source, plan/policy mismatch, traversal/symlink paths, unknown schemas, missing or corrupt receipts/refs, gaps in the verified prefix, and explicit terminal build failure. Assert refusal causes no worktree deletion, recipe launch, source reset, or publication.
- [ ] Run `uv run pytest tests/test_migration_recovery.py --no-cov -q`. Expected: RED for missing reconciliation/recovery.
- [ ] Implement recovery under the lease: verify repository/plan/policy; reconstruct the contiguous verified prefix from receipts and refs; import receipts newer than graph state; fail closed on allegedly completed but inconsistent evidence. Archive interrupted diagnostics, retire only owned incomplete workspaces after child liveness checks, increment generation, recreate the recipe from its predecessor, and reset only that recipe's attempt budgets.
- [ ] Persist publication intent before a source fast-forward under the repository publication lock. Reconcile source at either the recorded starting revision or exact intended target; reject everything else. Store the final result atomically. Do not interpret event-journal completion as verification authority.
- [ ] Run recovery, graph, worktree, process ownership, and pi tests. Expected: GREEN. Commit `feat: resume interrupted migrations from verified recipes`.

### Task 6: CLI resume and parent-profile integration

**Files:** Modify `src/refinery_ai/cli.py`, `src/refinery_ai/agents/repair_backend.py`, `src/refinery_ai/migrations/runner.py`, profile invocation wiring as necessary; create `tests/test_migration_resume_cli.py`, extend `tests/test_profile_run.py` and `tests/test_workflow_characterization.py`.

**Interfaces:** Add `resume_run_id: str | None = None` and `resume_bound_run: bool = False` to `MigrationRunner.run`, preserving every existing argument/default. Add `policy: MigrationPolicy | None = None` to its constructor and `migration_policy(settings: Settings) -> MigrationPolicy` in the backend factory module. Add CLI `--resume-run RUN_ID` to `migrate`; real profile callbacks use `resume_bound_run=True` for the plan pinned in their parent state.

- [ ] Add CLI tests: resume implies apply; loads the stored plan instead of latest; retains publication intent; rejects explicitly conflicting plan/profile/apply-result/agent policy; and rejects legacy/preview/terminal-failed runs. Distinguish explicit options from defaults with Click parameter-source metadata so `--no-apply-result` is not silently overridden, while an omitted flag inherits the recorded intent.
- [ ] Add real parent graph tests: kill a migration after one green recipe, restart the profile's pending callback, and assert that recipe does not repeat. Kill after child completion but before the parent checkpoint; assert the same stored child result returns. Two plausible child bindings must be an explicit error, never a guessed run.
- [ ] Add a parent `.stream(..., stream_mode=["tasks", "custom"], subgraphs=True)` test using real migration execution. Assert nested recipe/repair events and run/plan/profile identity are visible. Keep the compiled migration graph directly inspectable; do not replace the injected callback seam merely to draw a static parent graph.
- [ ] Run `uv run pytest tests/test_migration_resume_cli.py tests/test_profile_run.py tests/test_workflow_characterization.py --no-cov -q`. Expected: RED for missing public recovery/streaming integration.
- [ ] Implement explicit policy construction in the backend module. For new runs pin effective non-secret policy. For explicit resume recover the recorded policy unless a caller explicitly supplies a conflicting override. Treat values supplied through CLI, environment, or `.env` as explicit configuration; use Click parameter-source metadata and `Settings.model_fields_set` to distinguish them from omitted defaults. Omitted defaults inherit the manifest; live secrets are supplied afresh. Reject contradictions before side effects. Custom injected adapters need explicit policy metadata for resume rather than unverifiable constructor introspection.
- [ ] Bind production profile migration invocations to the unique run for their persisted plan, including stored completed results. Ensure independent migration persistence does not inherit the parent namespace but safe custom events still reach the parent consumer. Preserve existing ordinary standalone/profile/corpus behavior and public model golden files.
- [ ] Run CLI/profile/characterization, backend, corpus, graph, and recovery tests. Expected: GREEN. Commit `feat: expose safe migration resume through CLI and profiles`.

### Task 7: Documentation, whole-change review, and final verification

**Files:** Modify `README.md`, `evals/README.md` only where needed; add `docs/migration-execution.md`; update this plan's completion record after evidence exists.

- [ ] Document node/edge responsibilities, typed event fields, observer usage, journal paths/replay, and consuming nested LangGraph events. Explain that this ships reporting foundations rather than a viewer or heartbeat dashboard. Include a static Mermaid workflow diagram in the document.
- [ ] Document `uv run refinery migrate /path/to/project --resume-run RUN_ID`, run-ID discovery from artifacts/events, recipe-boundary restart, fresh agent calls, preserved checkpoints, override rules, POSIX ownership requirements, legacy/preview limitations, active-orphan refusal, and explicit terminal-failure semantics. Explain Git/graph reconciliation without claiming exactly-once source effects or command sandboxing.
- [ ] Run `uv run pytest`, `uv run mypy`, `uv run ruff check .`, and `uv run ruff format --check .`. Expected: tests and typing pass; changed files are style clean. Reproduce and report any baseline repository-wide style failures separately rather than performing unrelated cleanup. The earlier baseline was 384 passed/1 skipped with existing style failures; re-establish it at execution start because the repository may have changed.
- [ ] Verify `migrate --help`, ordinary preview, explicit resume error paths, and documentation commands without real model credentials. Inspect final diff, public result contracts, checkpoint/state serialization, and event payloads for secrets and accidental service-object persistence.
- [ ] Obtain an independent whole-change review focused on ownership, receipt/graph crash windows, publication reconciliation, and observer failure cleanup. Reproduce important findings with failing tests, fix, then rerun affected checks and the full suite. Record deferred minor findings and implementation rulings explicitly.
- [ ] Commit final documentation/completion record and report exact verification results, platform limitations, and whether any live Maven/pi/model smoke test was run. Preserve the feature branch/worktree for the user's integration choice.
