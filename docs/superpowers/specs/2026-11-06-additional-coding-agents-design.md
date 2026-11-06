# Additional coding agent backends

Extend Refinery's existing one-shot CLI repair approach to OpenCode, Codex CLI,
and Claude Code. Agents must already be installed, authenticated, and configured.
Internal repair and pi remain supported. No SDK integrations, installation,
session continuation, agent fallback, or internal tool-event integration is added.

## Approach

Continue the previously chosen one-shot approach, extracting shared transactional
repair execution from PiRepairAgent. Small per-agent adapters build commands and
map model/provider arguments. This preserves one implementation of snapshots,
rollback, bounded output, timeouts, cancellation, and owned child processes.
Separate complete implementations duplicate safety behavior; SDK/server adapters
add dependencies and session lifecycle work outside this iteration's scope.

## Selection and configuration

- Extend `--repair-backend` with `opencode`, `codex`, and `claude`.
- Retain `--coding-agent-model` and `--coding-agent-provider`. Pi uses its existing
  separate flags; OpenCode uses provider/model. Codex/Claude accept their native
  model identifier and inherit provider configuration. Unsupported explicit
  provider overrides fail before execution rather than being ignored.
- Add configurable executable paths for the new agents, retaining pi's existing
  setting. Agents are resolved only when selected for model repair.
- Keep deterministic repair first, existing model repair budgets, and strict-local
  policy rejection for external agents whose configuration Refinery cannot verify.
- Apply settings consistently to standalone migration, profile, and corpus runs.

## Permission policy

Per the user's decision, Refinery supplies unattended repair permissions by
default. Add `--coding-agent-permissions managed|inherit`, default `managed`,
with environment setting `REFINERY_CODING_AGENT_PERMISSIONS`.

`managed` supplies agent-specific permissions for editing and running commands:

- OpenCode: `run --auto`; explicit configured denials remain effective.
- Codex: `--ask-for-approval never` with `exec --sandbox workspace-write`.
  It retains sandbox enforcement; network or filesystem restrictions may still
  prevent particular build operations.
- Claude Code: `--permission-mode dontAsk --allowedTools Read,Edit,Write,Bash,Glob,Grep`.
  Tools outside that allowance still follow the agent's permission rules.
- Pi: retain its existing non-interactive behavior. The current adapter passes
  `--print --no-session` and no distinct permission-bypass flag; do not invent one.

`inherit` omits Refinery's permission/approval/sandbox overrides. It still passes
non-interactive/session/model arguments, preserves the agent's own settings, and
retains Refinery's timeout and process cleanup. It does not add an interactive
approval bridge or guarantee that the external agent will prompt or deny.
No agent is invoked with a blanket sandbox or permission bypass flag.

## Invocation and verification

Each repair starts a fresh process in the current isolated migration worktree.
Feed the prompt through stdin to avoid argument limits and exposing source or
failure details in process arguments. Verify OpenCode stdin support against its
published CLI implementation; if unavailable use a temporary prompt attachment
with deterministic cleanup, not shell interpolation. No user prompt is treated
as command syntax.

Exit success is only a candidate. Require actual source changes, reject Git
HEAD/index changes, and preserve transactional accept/rollback behavior. Refinery
runs its existing independent build verification. Agent errors, missing binaries,
timeouts, cancellation, and permission failures retain clear backend-specific
messages and clean up owned process groups and pending source snapshots.

## Persistence and compatibility

Record selected backend, executable, model/provider, and permission mode in the
migration policy, including CLI resume inheritance and corpus settings transport.
Reject explicit conflicting resume overrides. Interpret manifests predating the
permission option as `inherit`: that reflects the actual historical invocation,
rather than silently adding permissions when an old migration resumes. Existing
pi executable settings and public result contracts remain compatible.

## Validation

Use executable stubs for each CLI to verify arguments, stdin, working directory,
model mapping, opt-out, nonzero exits, no-change rejection, Git mutation rejection,
timeout/cancellation, and rollback. Cover CLI/config/corpus propagation, recorded
policy and old-manifest resume compatibility. Run existing pi/repair/migration
characterization tests, full suite, types, and changed-file style checks. Do not
run authenticated live agent/model calls without a separate explicit request.

## Sources checked

- https://opencode.ai/docs/cli/ (`run`, `--auto`, provider/model)
- https://learn.chatgpt.com/docs/developer-commands (Codex exec flags)
- https://learn.chatgpt.com/docs/non-interactive-mode (workspace-write)
- https://code.claude.com/docs/en/cli-reference (tool permissions, print, models)
- Installed Codex/Claude help; repository PiRepairAgent invocation.
