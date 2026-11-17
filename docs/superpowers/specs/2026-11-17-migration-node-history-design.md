# Migration node execution history

Implement the supplied brainstorming guidance as an observability enhancement in an isolated worktree. Keep the existing graph, node selection, highlighting and planned migration list.

## Approach

Reuse durable structured migration journals. Project compact execution history into run summaries on the server, then combine child histories at the profile Migration node. This avoids extra snapshot requests per child and preserves refresh/reconnect behavior. A frontend-only projection would require downloading every child journal; new dedicated events would duplicate existing lifecycle evidence. Add an optional `apply` field to events so previews cannot masquerade as applied or verified changes.

## Behavior

History accumulates one entry per step attempt, preserving repeated recipe executions and recovery generations. Profile cycles follow Migration node invocations; attempts count repeated recipes. Only recorded steps appear, never unexecuted planned migrations. Distinguish applied, verified, failed, skipped (only with explicit evidence), cancelled, interrupted, previewed, completed with unknown verification, and active with unconfirmed liveness. Failed verification may recover within an attempt; verified requires a successful checkpoint. Legacy data must not invent application or verification evidence.

Show the latest three entries, cycle and attempt labels, a summary, and an accessible expand/collapse button. Expanded history stays in a bounded scrolling area. Reserve enough graph-node space to avoid overlapping adjacent nodes; keep topology and routing. Use the same history component for the standalone migration execution node. Preserve historical entries when later cycles run. No changes to migration decisions, repair logic, or planned migration modes.

## Validation

Test real journal projection, persistence and preview evidence; repeated cycles/recovery, failures followed by repairs, cancellation, legacy events and duplicate delivery. Test history rendering, expansion, keyboard interactions and run isolation. Run monitor/backend regressions, frontend suite, typecheck and packaged production build. Review the complete diff before completion.
