# Migration Node History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Show durable, compact, expandable migration attempt history in the existing graph.
**Architecture:** Project structured journals into run summaries, combine children by parent invocation, and render bounded node history. Add optional application-mode evidence to events.
**Tech Stack:** Python/Pydantic, React/TypeScript, existing Dagre/React Flow, pytest/Vitest.
**Spec:** docs/superpowers/specs/2026-11-17-migration-node-history-design.md

## Global Constraints

- Work only in the managed worktree; preserve existing checkout/processes.
- No migration routing or execution behavior changes; no log parsing.
- Planned migrations stay separate from execution history.
- Three latest entries initially; bounded expandable history.
- Never infer successful verification or worker liveness without evidence.

## Review Focus

- Preview checkpoints must not appear applied or verified (Task 1).
- Recovered child runs can span parent invocations (Tasks 1, 2).
- Failure followed by repair retains attempt identity and truthful status (Task 1).
- Legacy and partial journals remain useful without invented success (Task 1).
- Expanding history preserves graph selection and non-overlapping layout (Task 2).

### Task 1: Durable attempt projection

**Files:** monitor/history.py, monitor/models.py, monitor/journals.py, migrations/events.py; tests/test_monitor_history.py, tests/test_migration_events.py.
**Interfaces:** `migration_history(events: list[MonitorEvent]) -> list[NodeExecutionEntry]`; `RunSummary.execution_history`; entry identity, recipe/name, step, generation, parent_execution_id, attempt, status. Optional event `apply`.
- [x] Write failing tests for applied versus verified/preview, repair/failure, replay, retries, explicit skips, terminal events and legacy records.
- [x] Run targeted pytest; observe expected missing behavior.
- [x] Implement projection and optional evidence; attach to snapshot summaries.
- [x] Run monitor and migration regressions; commit.

### Task 2: Compact graph history

**Files:** web/monitor/src/{state.ts,executionHistory.ts,NodeHistory.tsx,WorkflowGraph.tsx,styles.css}; matching test files.
**Interfaces:** `nodeExecutionHistory(state, phase, runs)` returns entries with display cycle/attempt; reusable `NodeHistory` component. Graph consumes run summaries already polled by App.
- [x] Write failing tests for multi-cycle child aggregation, isolation, replay/legacy behavior, status summary, three-entry collapse and expansion interactions.
- [x] Run Vitest; observe expected missing behavior.
- [x] Implement typed aggregation, accessible node component and graph sizing.
- [x] Run frontend tests, typecheck, production build and backend suite; commit source and packaged assets.

### Task 3: Review and finish
- [x] Run fresh whole-change review; address substantive findings with regression tests.
- [x] Verify final checks and leave implementation in its separate worktree, without merging or starting/stopping existing services.


## Execution and verification record

Implemented inline in the managed worktree, using the user's supplied behavior as the design brief. Commits consolidated at completion. The projection helper is named `executionHistory.ts` to avoid a case-insensitive import collision with `NodeHistory.tsx` on macOS.

- Backend baseline: 7 tests passed; frontend baseline: 19 passed.
- Backend feature RED: 13 tests failed on absent history/application-mode evidence; GREEN: 20 passed with existing migration event tests.
- Frontend feature RED: 6 behavior assertions failed against empty component/projection; GREEN: 27 tests passed including actual React Flow keyboard inspection integration.
- Monitor/migration regression suite: 109 passed.
- Full repository suite: 531 passed, 1 skipped, 6 failures below. The same six fail on an untouched archive of base commit `33d0266` (68 other AI-repair tests pass).
- TypeScript typecheck, Python history mypy check, changed-file Ruff checks, production UI build and git diff whitespace checks pass.
- Browser verification against synthetic journals on a separate temporary monitor: latest-three history, expansion, node inspection, non-overlapping graph layout, and persistence after refresh confirmed. Temporary preview server stopped.
- Fresh independent review: no actionable correctness/regression findings. Existing summary labels remain outside this feature; generated assets were rebuilt and served in the browser.

### Existing environment-dependent test failures

All raise OpenAI missing-credentials errors while constructing their provider client:

- `tests/test_ai_repair.py::test_repair_chain_uses_ai_after_deterministic_rules_do_not_match`
- `tests/test_ai_repair.py::test_ai_repair_satisfies_framework_neutral_contract`
- `tests/test_ai_repair.py::test_ai_repair_uses_scoped_full_file_replacement`
- `tests/test_ai_repair.py::test_ai_repair_rejects_a_noop_replacement`
- `tests/test_ai_repair.py::test_ai_repair_rolls_back_an_unresolvable_dependency`
- `tests/test_ai_repair.py::test_ai_repair_can_fix_generated_annotations_without_residual_java`

Automatic approval review rejected a proposed dummy-credential rerun over possible external model requests. No credentials were configured; verification instead reproduced the failures on the untouched base. No merge, push, or changes to the original checkout were performed.
