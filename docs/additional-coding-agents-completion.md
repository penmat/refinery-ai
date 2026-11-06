# Additional coding agents completion

OpenCode, Codex CLI, and Claude Code now share pi's transactional CLI repair
execution. Each runs once per attempt in the migration worktree; Refinery retains
independent verification, acceptance, rollback, repair budgets, process ownership,
and bounded output. Existing internal/pi choices remain available.

New invocations default to managed permission flags. The inherit option omits
Refinery's permission/approval/sandbox overrides. Backend, executable paths,
model/provider, and permission mode are transported through standalone, profile,
and corpus execution and recorded for resume. Historical manifests default to
inherit so a resume does not gain new permissions.

## Decisions made during implementation

- OpenCode uses file-backed non-TTY stdin, as pi does. Its upstream CLI code and
  maintainer discussion confirm that input contract:
  https://github.com/anomalyco/opencode/issues/16283. Old releases with the reported
  regression can fail explicitly; no prompt/source text is added to command args.
- Disabled AI patching still launches the ordinary corpus profile child; it
  disables coding-agent repair. Suppressing all subprocesses would break corpus
  execution. The plan's wording was interpreted accordingly.

## Validation

- Final full suite after review fixes: 484 passed, 1 skipped in 105.55 seconds.
- Mypy: 113 source files passed.
- Changed Python files pass Ruff lint/format; whitespace diff check passes.
- CLI help checked for migrate, run, and run-corpus. Installed Codex/Claude also
  accepted the planned commands in help-only checks, without model calls.
- Executable stubs verify arguments, permissions, stdin, source transactions,
  Git mutation rejection, missing binaries, timeout, cancellation, bounded output,
  and descendant termination. CLI/corpus tests verify option propagation and
  historical manifest settings/conflicts.

No authenticated model/provider or real Maven smoke test was run. OpenCode is not
installed in this environment. The integration targets CLIs supporting the
published flags and reports unsupported flags instead of weakening permissions.
POSIX remains required. External configuration, model defaults, credentials,
permissions and agent versions are not snapshotted by Refinery.

Repository-wide style checks retain the pre-existing E501 in
`deterministic_repair.py:1478` and 34 pre-existing files needing formatting;
unrelated code was left unchanged.

## Independent review and corrections

Four important findings were reproduced and fixed:

- Pin child `PWD` as well as cwd to the candidate worktree. A stale caller `PWD`
  could cause OpenCode to edit outside the transaction.
- Force OpenCode `--standalone` in both permission modes, keeping its ordinary
  server work within the owned invocation instead of a shared background service.
  The flag is defined in its current official command schema:
  https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/cli/src/commands/commands.ts.
- Reconcile each bound profile child's policy before constructing adapters; older
  runs retain inherit permissions. Corpus omission also stays omitted in child
  commands, so new defaults do not become explicit resume conflicts.
- Validate explicit resume settings after recorded defaults are inherited. Repeating
  a matching provider while omitting the recorded model now succeeds.

No minor review findings were deferred.

Review exclusions were evaluated as follows:

- No live authenticated/model/Maven testing: explicitly excluded; external integration
  behavior still needs an operator smoke test.
- Obsolete CLI compatibility: unsupported flags fail explicitly; older installations
  may need an upgrade.
- External configuration/credentials: remain owned by the operator; externally changed
  defaults cannot be reconstructed by resume.
- Legacy goal-only run: preserve the existing path, as this feature targets migrate,
  profile run and corpus; these backends require one of those supported paths.
- Deliberately detached arbitrary daemons: remain outside process-group containment;
  those processes can outlive cleanup. Ordinary OpenCode server work is addressed
  through mandatory standalone execution.

Final changed-file checks: 13 Python files pass Ruff lint/format; mypy passes
all 113 source files; CLI help and `git diff --check` pass.
