# Migration subgraph completion

The migration runner now delegates to an explicit LangGraph workflow, with separate
baseline, recipe, verification, repair, checkpoint, publication, and finish phases.
Versioned structured events are persisted to an append-only journal and forwarded
to observers and parent LangGraph custom streams. Preview remains non-durable.

Recovery retains the verified recipe prefix and starts a fresh graph generation
for the interrupted recipe. Standalone CLI and production profile callbacks use
the same manifest, receipts, checkpoint store, and publication reconciliation.

## Implementation rulings

- A shared Git-repository invocation lease replaces separate per-run/publication
  locks. It conservatively serializes apply runs, including alternate artifact
  configurations and linked checkouts; independent clones remain independent.
- A bootstrap retains the lock and records its process group before command exec.
  Git, builds, pi, and dependency validation use guarded launches during migration.
  Existing groups cause refusal; recovery never kills arbitrary recorded PIDs.
- Recovery validates external state before beginning a new graph generation,
  rather than replaying a pending mutating node from its checkpoint. The explicit
  recover node prepares the reconciled runtime workspace. Recovery preflight
  errors therefore occur before graph lifecycle events for that generation.
- Graph/reporting integration was committed together because rollback and event
  failure handling share node boundaries.
- Explicit environment/settings overrides as well as CLI flags must agree with
  recorded policy. Omitted settings inherit the manifest; secrets are supplied
  afresh. Custom repair adapters require explicit policy metadata to resume.

## Independent review

The final review found five important issues, all addressed: unowned Git children,
loss of interrupted diagnostics, symlinked worktree ancestors, locks scoped to
artifact configuration, and unverifiable custom repair policies. Regression tests
cover ownership registration, alternate-directory exclusion, symlink refusal,
retained interrupted source patches, and missing custom policy rejection.

## Verification and limits

The suite includes real Git/SIGKILL crash windows after verification, commit,
receipt, publication, and result persistence; profile recovery; refusal cases;
nested event streaming; long preview; and reporter-failure rollback. Existing
result/progress and repair behavior remain covered by characterization tests.
The proposed test inventory was consolidated rather than implemented verbatim;
there is no dedicated kill-at-bootstrap-handshake or stale-PID-reuse test.

Safe ownership/resume requires POSIX. These controls do not sandbox commands or
contain deliberately detached external daemons. There is no viewer or heartbeat
dashboard in this iteration. No live Maven/pi/provider/model smoke test was run.

Repository-wide lint retains the pre-existing line-length failure at
`deterministic_repair.py:1478`; repository-wide format checking reports 34
pre-existing files. Changed Python files pass lint and format checks.

Final verification: **418 passed, 1 skipped** in 76.08 seconds; mypy passed
for 111 source files; all 26 changed Python files passed Ruff lint/format;
`git diff --check` passed; CLI help exposes `--resume-run`. The existing
repository-wide lint failure was reproduced on the main checkout.
