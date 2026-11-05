# Selectable coding agents for migration repair

## Intent

Improve migration repair reliability by allowing an installed coding agent to
investigate and edit the migration worktree. Preserve the current minimal,
selected-file implementation for users who need its smaller disclosure scope.

## Goals and scope

- Offer `internal` (default, current implementation) and `pi` repair backends.
- Require external agents to be installed and configured by the user. Do not
  install packages, manage authentication, or require Refinery model credentials
  when using pi.
- Allow optional per-invocation provider and model overrides without changing pi's
  saved defaults. Otherwise use pi's configured model and credentials.
- Keep deterministic repair first and retain Refinery's verification, attempt
  budgets, checkpointing, and publish rules.
- Support future adapters without implementing OpenCode, Codex, or Claude now.

## Approaches

The user selected a one-shot CLI adapter: one external process per repair attempt.
This fits the existing synchronous repair loop and an externally installed agent.
A persistent RPC adapter would support richer interaction and session continuity,
but adds protocol and lifecycle management unnecessary for this iteration.
Embedding pi's SDK would introduce a Node integration and package dependency,
working against the externally installed-agent requirement.

## Contract and integration

Use the existing `MigrationRepairer` and transactional accept/rollback contracts,
with one backend factory shared by migration entry points. The internal adapter
retains its existing scoped proposals and validation. Pi edits files directly.
Both feed the existing repair stages; the runner remains responsible for judging
verification improvement and retaining or reverting a candidate.

Backend selection is independent of repair policy (`disabled`, `local`, or
`configured-provider`). Disabled means no model repair and no external process.
The existing local policy validates a loopback endpoint for internal repair.
For this iteration, reject pi with local policy explicitly: a provider/model name
cannot prove pi's effective endpoint is local. Local models remain usable through
pi's own configuration under configured-provider policy. Explain this distinction
in configuration documentation; never silently weaken the local policy.

## Configuration

Proposed implementation choices:

- `REFINERY_REPAIR_BACKEND=internal|pi` with CLI `--repair-backend` on migration
  entry points, including the combined workflow and corpus propagation.
- `REFINERY_CODING_AGENT_MODEL` and `REFINERY_CODING_AGENT_PROVIDER`, both optional,
  with matching CLI overrides. Keep these separate from the existing internal
  `REFINERY_REPAIR_MODEL` so its default cannot override pi accidentally.
- `REFINERY_PI_EXECUTABLE=pi` allows an explicit executable path, not a shell
  command or argument string.
- Use the configured command timeout for each agent attempt and bound captured
  diagnostics with the existing output limit.

## Pi invocation

Invoke the executable directly, without a shell, in the isolated migration
worktree. Use print mode with no saved session and send the repair task on stdin.
Pass provider/model flags only when specified. Each attempt starts fresh with the
latest accepted worktree contents and current build diagnostics.

The task identifies the recipe, current failure, and project root. Ask pi to
preserve application behavior, repair sources rather than weaken checks or tests,
and avoid committing, staging, or publishing. Pi may inspect the whole project
and execute build commands, as authorized by the user. Its existing global
configuration and credentials remain in effect. Do not automatically opt into
project-local executable extensions beyond pi's configured trust behavior.

A Git worktree isolates changes for review; it is not a filesystem or process
sandbox. The adapter does not claim to restrict agent commands to that directory.

## Transaction and failure handling

Snapshot the pre-attempt source state, including earlier recipe changes and
accepted repairs. Rollback must restore edited/deleted files, remove new source
files, and preserve executable modes and symlinks. Preserve the Git index and
HEAD; detect unexpected agent changes to them and fail the attempt. Do not use
`reset --hard HEAD` as a substitute for restoring pre-attempt changes.

Generated build directories follow existing migration exclusions and are not
candidate source changes. Exact snapshot mechanics are left to implementation,
but must handle tracked files and pre-existing untracked source files without
following symlinks outside the worktree.

Missing executables, invalid overrides, nonzero exit, timeout, and no source
changes produce actionable failed repair results. Revert partial edits before
returning failure. On timeout or cancellation terminate the process group so
agent-spawned build processes cannot keep editing after rollback. Retain bounded
stdout/stderr in repair diagnostics and report the selected backend. Do not
invent token usage when the external agent does not provide it.

Successful process exit means a candidate was applied, not that the migration
passed. Refinery independently verifies it. Keep candidates that pass or improve
the existing failure score; otherwise restore the pre-attempt state. Only fully
verified migrations become publishable checkpoints. Never silently switch agents.

## Validation and documentation

Test backend selection and policy combinations, CLI/environment propagation,
argument construction and stdin, missing executable and nonzero exits, no-op
results, bounded diagnostics, and timeout cleanup. Use a fake executable for
process integration tests; no live model or credentials are required.

Exercise rollback against real temporary Git repositories with pre-existing
recipe changes, additions, deletions, binary files, symlinks, modes, and staged
state. Verify acceptance and rejection through the runner. Run existing internal
repair regressions and repository-required formatting, typing, and test checks.

Document installation as a prerequisite, inherited pi configuration, provider/model
overrides, policy interaction, timeout behavior, and the difference in disclosure
scope between internal and pi. Live pi/model smoke testing is optional and must
be reported separately from fake-agent integration coverage.

## Left to implementation

Module names, snapshot storage and cleanup, precise prompt wording, diagnostic
format, and test organization. Avoid a plugin registry, persistent sessions, or
new orchestration framework for two adapters.
