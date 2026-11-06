# Migration execution subgraph

## Intent

Make Refinery's migration execution explicit in LangGraph and expose structured,
reliable execution events for future visualization. The user selected a migration
subgraph rather than leaving migration orchestration hidden inside the profile
graph's single `migrate` node.

## Scope

Replace the imperative orchestration in `MigrationRunner.run` with a compiled
migration graph. Keep its public API, result models, CLI behavior, recipe runners,
Git worktree policy, repair budgets, and progress messages compatible.

Safe mid-migration resume is included. The user selected recovery from the last
verified recipe checkpoint: preserve completed recipes and restart only the
interrupted recipe. Continuing inside a partially executed recipe or pi session
is outside this iteration.

Deliverables include the graph, typed lifecycle events, persisted apply-run event
history, a subscription interface, and safe recipe-boundary recovery. A terminal/
browser viewer, corpus graph, pi tool-level events, new hosting services, and a
new metrics backend are outside this iteration.

## Approaches

1. Keep the existing runner and emit events around its internal branches. This
   improves visibility but leaves the execution structure and nested retry loops
   implicit. It does not deliver the architecture the user requested.
2. Extract a migration subgraph, retaining the existing effectful services. This
   is the selected approach: graph nodes and conditional edges own workflow
   sequencing; builds, Git operations, recipe execution, and agents remain ordinary
   Python implementations.
3. Add a durable, automatically resumable migration engine at the same time.
   The user requested safe resume but selected a bounded version of this approach:
   recover at verified recipe boundaries. Reconcile Git and graph persistence,
   and discard partial recipe effects. Exact continuation within an interrupted
   recipe remains deferred.

## Architecture and responsibilities

`MigrationRunner` remains the compatibility facade: dependencies and configuration
are supplied through its existing constructor, and `run(...)` returns `MigrationRun`.
It prepares an invocation and delegates orchestration to the compiled graph.
There must not be a second, fallback imperative orchestration path.

The graph lives under `workflows`, following the existing profile and planning
graphs. Runtime service objects are held in an invocation-scoped execution context,
not serialized into graph state. Graph state carries typed execution data: plan,
run identity, step index, JDK, source/checkpoint revisions, current workspace,
verification results, repair stage/attempt, candidate outcome, and step results.
Represent workspace data using paths and revisions, not live Git manager objects.

Split baseline, per-step, and reporting support into focused modules as needed;
avoid moving the entire 600-line method unchanged into one graph node or another
single oversized orchestration class.

## Nodes and routing

The proposed public execution phases are:

- `prepare_run`: load/validate the plan, assign run identity, resolve required
  JDKs, and initialize isolated worktree management for apply mode.
- `recover_run`: on resume, validate ownership and persisted receipts, retain
  the verified prefix, archive incomplete recipe artifacts, and prepare a fresh
  attempt for the first unverified recipe.
- `verify_baseline`: verify the untouched repository, or reuse a matching trusted
  checkpoint. Preserve the existing JDK fallback order and baseline artifacts.
- `prepare_step`: select the next recipe and create its isolated worktree.
- `execute_recipe`: dispatch exactly one recipe using the existing runner seam.
- `verify_candidate`: verify the recipe or the latest successful repair candidate,
  using the required post-recipe JDK.
- `select_repair`: select the next eligible stage and bounded attempt according
  to the existing deterministic-first policy and applicability checks.
- `repair`: execute one repair attempt using the selected implementation.
- `evaluate_repair`: accept an improved/passing transactional candidate, or restore
  a non-improving candidate. Preserve the existing failure-score definition.
- `checkpoint_step`: persist one green recipe result and its verified revision.
- `record_step_failure`: persist failure diagnostics and remove only the failed
  owned worktree, retaining earlier verified checkpoints.
- `publish_result`: optionally fast-forward the source to the last verified change,
  including after a later recipe fails, as it does today.
- `finish_run`: persist the run result and return the existing `MigrationRun`.

Edges express progression, repair retries/stage advancement, stopping on failure,
and moving to the next recipe. Individual repair attempts are separate graph
executions of `repair`, not a hidden loop inside it. Baseline fallback builds may
remain inside `verify_baseline`, but each attempted JDK/build emits a typed detail
event. The graph recursion limit is derived from plan length, repair-stage budgets,
and a fixed routing allowance; it must not fail on a valid plan with many recipes.

Dry runs skip baseline/build verification, repair, checkpointing, and publication,
while retaining today's recipe preview dispatch, result shape, and progress text.
Empty plans and unknown execution types retain current behavior.

## Parent graph integration

Profile runs continue to invoke migrations through `ProfileRunner.migrate` and
its trusted-checkpoint handoff. The migration graph executes in that parent
LangGraph context, allowing nested lifecycle events to be consumed without parsing
the progress callback. Standalone `migrate` uses the same compiled graph.

Expose the compiled migration graph for inspection of its nodes and edges.
Nested execution/stream propagation must be verified with a real parent graph,
not inferred from graph construction or mocked callbacks.

The migration graph owns its persistence and stable run identity; do not inherit
its parent's checkpoint namespace accidentally. Existing profile-level checkpointing
remains in place. When a resumed profile re-enters its pending migration callback,
recover the unique unfinished migration for that exact persisted plan and policy
rather than silently beginning duplicate execution. If that child finished before
the parent recorded its return value, return the stored child result after
validating the published revision. Preserve the injected callback seam for tests
and custom callers; implement recovery in the real migration invocation path.

## Safe resume and persistence

Recovery is supported for new apply runs created by this implementation, not
legacy artifact directories with incomplete provenance. Preview runs are not
resumable. A normal invocation still starts a new run unless an explicit resume
is requested or the existing profile graph is resuming its bound child invocation.

Expose `refinery migrate REPOSITORY --resume-run RUN_ID` and a corresponding
runner argument. Resume implies apply, reads the pinned original plan rather
than the latest plan, and retains the original publication intent. Explicitly
conflicting plan, profile, apply-result, backend, model/provider, repair policy,
or attempt-budget overrides are rejected. Authentication secrets are never
persisted. External model defaults may change outside Refinery; explain that a
restarted recipe can make fresh model calls and does not resume a pi session.

Persist typed, versioned migration state through a dedicated LangGraph SQLite
checkpointer, using synchronous checkpoint durability and stable run/recovery
identities. Keep live repairers, locks, service clients, and subprocess handles
out of serialized state. Record a run manifest before the first migration side
effect, including canonical repository identity, starting revision, immutable
plan content/digest, ordered steps, effective repair policy/budgets, publication
intent, and reference to the checkpoint store. Publish manifests and receipts
atomically. Do not rely on a human-readable event journal as recovery authority.

Recovery reconciles external effects before continuing any graph checkpoint:

1. Acquire exclusive run ownership. Refuse to resume while the original worker
   or a source-mutating child process is active. An inherited process lease or
   equally verifiable ownership mechanism must cover the build/agent children,
   not just the Python parent PID. A stale PID number alone is not proof of
   ownership; account for PID reuse. An inherited lease alone is insufficient
   if an external CLI closes it before launching descendants. Retain process-group
   identity where needed and fail closed while that group may still be alive;
   never kill an unrelated process merely because it reused an old PID.
   Source checkout publication must also be
   serialized against competing migration runs. If safe ownership cannot be
   established, report the blocking condition without deleting worktrees.
2. Validate repository, source checkout cleanliness, pinned plan digest, policy,
   and schema version. Source HEAD must still equal the starting revision or an
   exactly recorded publication result from this run; refuse unrelated movement.
3. Reconcile the longest contiguous verified recipe prefix. Accept a recipe as
   completed only when its atomic completion receipt binds successful verification,
   the exact checkpoint revision/ref, plan step, predecessor revision, and JDK.
   Existing completed-step metadata can be extended for this receipt. No-op green
   recipes need receipts too: their checkpoint revision may equal their predecessor.
4. If a verified receipt exists but the graph checkpoint was not saved, import
   that completed step into recovered state without re-running it. A Git commit
   or ref without a matching verified receipt is insufficient: preserve its
   diagnostics and restart that recipe from the preceding verified revision.
   Missing/corrupt evidence for an allegedly completed recipe fails closed rather
   than claiming success. Recovery may not skip gaps in the verified prefix.
5. Retain the interrupted attempt's diagnostics separately before removing its
   owned worktree. Never reset the user's source checkout. Recreate the interrupted
   recipe's workspace from the last verified revision, using a fresh attempt
   identity so files/artifacts cannot collide with the abandoned attempt. Restore
   the JDK from that verified prefix. If no verified baseline receipt exists,
   repeat baseline verification on an untouched disposable checkout.
6. Re-enter the graph through an explicit recovery route at that recipe boundary,
   not blindly through a pending `repair`, verification, commit, or publish node.
   In-memory repair snapshots cannot be trusted after a process crash. Reset the
   interrupted recipe's repair budgets for its fresh execution, retaining prior
   attempt events/history. Do not automatically repeat explicit terminal build
   failures; this resume path is for interrupted/incomplete runs.
7. Reconcile publication. Record publication intent and the verified target before
   fast-forwarding. If source HEAD already equals that target after a crash, mark
   publication completed without merging again. If it is still the starting
   revision, publication can proceed after validation. Any other HEAD fails closed.
   A fully finished run returns its stored result without repeating side effects.

The graph checkpoint database and Git filesystem are not one transaction. Receipts
and validation make those gaps recoverable; checkpointing alone does not provide
exactly-once Git or process execution. No automatic retries of non-idempotent nodes
are permitted. All automatic profile recovery uses these same checks.

Local POSIX process/file locking is the proposed initial ownership mechanism,
matching the existing pi platform support. The implementation must document and
explicitly reject unsupported resume platforms rather than simulate a safe lock.
Normal non-resume behavior on other platforms should remain supported where it
already works. Child-process lease integration and exact state-store module
boundaries are implementation details, but their safety behavior requires real
process tests.

## Structured reporting

Provide a typed, versioned `MigrationEvent` contract and an optional observer
callback on the runner. Emit from node execution and typed results, never from
parsing human-readable progress messages. LangGraph custom streaming carries the
same safe event envelope when invoked inside a parent graph; normal LangGraph
task/update streaming remains available for graph-aware consumers.

Each event contains:

- schema version, migration run ID, plan ID, repository/profile identity;
- monotonic per-run sequence, UTC timestamp, event ID, and node-execution ID;
- phase/node and lifecycle (`started`, `completed`, `failed`, `cancelled`,
  `interrupted`);
- optional recipe step/index, repair stage/attempt, recovery generation, JDK,
  and domain outcome;
- completion duration and optional artifact/checkpoint references.

Keep returned nonzero build/repair outcomes distinct from unexpected exceptions:
a verification node can complete with a failed build, then correctly route to
repair. Acceptance and rollback are explicit outcomes, not inferred from logs.
Started/completed events sharing an execution ID identify repeated loop visits.

Do not include prompts, source contents, credentials, raw model responses, complete
build output, or serialized service objects in lifecycle events. Point viewers
to the existing diagnostic artifacts where appropriate. Run/step identity and
attempt counters must be present when available, including on error paths.

Persist apply-run events synchronously as newline-delimited JSON, with one writer
per migration invocation, under
`<repository>/<state_directory>/migration-events/<run_id>.jsonl`. This separate
directory avoids changing the existing worktree manager's run-directory creation
contract. Close and flush the journal on every exit path. On resume, retain the run ID,
continue event sequence numbers, mark unmatched prior operations as interrupted,
and emit explicit recovery/restarted-recipe events with a new generation.
Journal replay must
tolerate an incomplete trailing record after a crash and validate schema versions
and sequence ordering. Do not attempt multi-process concurrent writes to one file.

Preview runs may emit observer/stream events but do not create an event journal
by default, preserving their existing filesystem behavior. No public result-model
field is required to find a journal: its path is derived from the existing run ID.

Event persistence failures stop execution before further side effects and surface
as explicit errors. Reporter failures must never bypass pending repair rollback
or process cleanup, and a post-publication reporting error must not attempt to
undo a verified published revision. Optional observer callback errors are isolated
from migration execution and surfaced through diagnostic logging; the journal is
the reliable replay source while healthy. Event persistence and source/Git effects
are not one atomic transaction, so the last event after a crash may describe an
operation whose outcome is unknown. Do not report it as successful or automatically
repeat it based only on missing completion events.

OpenTelemetry/LangSmith tracing remains optional and complementary. Standard graph
instrumentation and these stable events provide the boundaries for later timing
and dashboard work without making telemetry export part of correctness.

## Failure and resource policy

Preserve transactional agent cleanup, including pi process-group termination,
restoration of partial edits, and rollback when verification raises or is
interrupted. A transactional candidate pending between graph nodes must be
restored if execution exits before acceptance, including reporter failures.
Graphs must not automatically retry non-idempotent nodes.

Preserve the current exception-versus-domain-failure behavior unless a change is
required for cleanup and is covered by a regression test. Failure events are
emitted when possible before propagating exceptions; reporting errors must not
mask the original exception or prevent cleanup. Abrupt termination such as
SIGKILL cannot promise a final event; recovery closes that unknown operation as
interrupted rather than inventing its result. Liveness/heartbeat display is deferred,
while ownership/liveness checks needed for safe recovery are included.

## Validation

Run existing migration, baseline/JDK, worktree, profile, repair, pi, and workflow
characterization tests without changing expected results simply to fit the graph.
Add real graph tests for preview and apply, successful and failed baseline,
multiple recipes, stage exhaustion, rejected repair retries, improved intermediate
candidates, final success, failure artifact retention, earlier-checkpoint publish,
and interruption cleanup.

Assert graph structure has the meaningful phases and repair loop edges. Exercise
a plan longer than the default LangGraph recursion allowance. Verify event order,
stable identities, attempt details, domain outcomes, exception/cancellation events,
journal replay, incomplete trailing records, sink failures, observer isolation,
and absence of sensitive content. Verify nested streaming through the real profile
graph as well as standalone execution.

Use subprocess fault-injection tests that actually terminate workers between
recipe verification, commit/ref creation, receipt persistence, graph checkpoint
persistence, publication, and finalization. Prove previously verified recipes do
not repeat, an interrupted recipe starts from its predecessor, graph/receipt gaps
reconcile, unchanged-revision no-op steps survive, and completed publication is
not repeated. Test a live orphan child, two simultaneous resume attempts, dirty or
moved source checkout, altered plans/policies, missing/corrupt refs or receipts,
unsupported schema versions, and terminal failure rejection. Test recovery via
standalone CLI and the real parent profile callback.

Run the full test suite, mypy, and style checks for changed files; report existing
repository-wide style failures separately. No live model credentials are required.

## Implementation choices left open

Exact module boundaries and graph-state field names, event replay helper signature,
internal context representation, and the mechanics of stream forwarding. Use the
installed LangGraph version and existing dependencies. No additional orchestration
framework, external collector, dashboard server, or distributed event bus is needed.
