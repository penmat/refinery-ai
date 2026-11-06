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

- Full suite: 474 passed, 1 skipped in 104.87 seconds.
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
