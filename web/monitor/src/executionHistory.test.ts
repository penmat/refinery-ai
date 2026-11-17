import { describe, expect, it } from "vitest";
import { applyEvents, emptyState, type MonitorEvent, type Run } from "./state";
import { nodeExecutionHistory } from "./executionHistory";

const visit = (sequence: number, execution_id: string): MonitorEvent => ({
  schema_version: 1,
  run_id: "parent",
  sequence,
  event_id: `e${sequence}`,
  execution_id,
  timestamp: "2026-11-17T00:00:00Z",
  phase: "migrate",
  lifecycle: "started",
  generation: 0,
});
const child = (id: string, parent: string, history: unknown[]): Run =>
  ({
    run_id: id,
    kind: "migration",
    profile: null,
    parent_run_id: "parent",
    parent_execution_id: parent,
    status: "unknown",
    first_seen: null,
    last_seen: null,
    event_count: 1,
    diagnostics: [],
    execution_history: history,
  }) as Run;
const entry = (id: string, parent: string, generation = 0) => ({
  id,
  node: "migrate",
  run_id: "child",
  parent_execution_id: parent,
  generation,
  step: 1,
  recipe: "java17",
  label: "Java 17",
  attempt: 1,
  status: "verified" as const,
});

describe("node execution history", () => {
  it("keeps cycles and repeated recipe attempts across child runs in parent order", () => {
    const state = applyEvents(emptyState(), [
      visit(1, "first"),
      visit(2, "second"),
    ]);
    const runs = [
      child("b", "second", [entry("two", "second")]),
      child("a", "first", [entry("one", "first")]),
    ];
    expect(
      nodeExecutionHistory(state, "migrate", runs).map((e) => [
        e.id,
        e.cycle,
        e.attempt,
      ]),
    ).toEqual([
      ["one", 1, 1],
      ["two", 2, 2],
    ]);
    expect(nodeExecutionHistory(state, "plan", runs)).toEqual([]);
  });
  it("preserves original parent linkage when a child resumes in a later cycle", () => {
    const state = applyEvents(emptyState(), [
      visit(1, "first"),
      visit(2, "second"),
    ]);
    const run = child("child", "second", [
      entry("one", "first"),
      entry("two", "second", 1),
    ]);
    expect(
      nodeExecutionHistory(state, "migrate", [run, run]).map((e) => [
        e.cycle,
        e.attempt,
      ]),
    ).toEqual([
      [1, 1],
      [2, 2],
    ]);
  });
  it("does not leak history from another profile or infer history from plans", () => {
    const state = applyEvents(emptyState(), [visit(1, "first")]);
    const other = {
      ...child("child", "first", [entry("one", "first")]),
      parent_run_id: "other",
    };
    const legacy = child("legacy", "first", []);
    delete legacy.execution_history;
    expect(nodeExecutionHistory(state, "migrate", [other, legacy])).toEqual([]);
  });
  it("shows standalone generations as cycles and survives replay", () => {
    const state = applyEvents(emptyState(), [
      { ...visit(1, "first"), run_id: "child" },
    ]);
    const run = child("child", "parent", [
      entry("one", "parent"),
      entry("two", "parent", 1),
    ]);
    expect(
      nodeExecutionHistory(state, "execute_recipe", [run]).map((e) => e.cycle),
    ).toEqual([1, 2]);
    expect(
      nodeExecutionHistory(applyEvents(state, state.events), "execute_recipe", [
        run,
      ]),
    ).toEqual(nodeExecutionHistory(state, "execute_recipe", [run]));
  });
});
