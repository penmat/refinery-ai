import { describe, it, expect } from "vitest";
import {
  applyEvents,
  emptyState,
  summarize,
  nodeStatus,
  nodeMigrationContext,
  type MonitorEvent,
} from "./state";
const event = (
  sequence: number,
  extra: Partial<MonitorEvent> = {},
): MonitorEvent => ({
  schema_version: 1,
  run_id: "a".repeat(32),
  sequence,
  timestamp: `2026-11-06T10:00:0${sequence}Z`,
  event_id: `e${sequence}`,
  execution_id: "visit1",
  phase: "verify_candidate",
  lifecycle: "started",
  generation: 0,
  ...extra,
});
describe("execution history", () => {
  it("replays incrementally and deduplicates reconnects", () => {
    const events = [
      event(1),
      event(2, { lifecycle: "completed", outcome: "verification_failed" }),
      event(3, { execution_id: "visit2", generation: 1 }),
    ];
    const all = applyEvents(emptyState(), events);
    const live = applyEvents(
      applyEvents(emptyState(), events.slice(0, 2)),
      events,
    );
    expect(live).toEqual(all);
    expect(all.events).toHaveLength(3);
    expect(nodeStatus(all, "verify_candidate")).toBe("unconfirmed");
    expect(all.visits.visit1.outcome).toBe("verification_failed");
  });
  it("keeps failed builds distinct from exceptions", () => {
    expect(
      nodeStatus(
        applyEvents(emptyState(), [
          event(1),
          event(2, { lifecycle: "completed", outcome: "verification_failed" }),
        ]),
        "verify_candidate",
      ),
    ).toBe("needs attention");
    expect(
      nodeStatus(
        applyEvents(emptyState(), [
          event(1),
          event(2, { lifecycle: "failed" }),
        ]),
        "verify_candidate",
      ),
    ).toBe("failed");
  });
  it("does not invent tokens, cost, elapsed completion or worker liveness", () => {
    const state = applyEvents(emptyState(), [event(1)]);
    expect(summarize(state).tokens).toBeNull();
    expect(summarize(state).cost).toBeNull();
    expect(summarize(state).elapsed).toBeNull();
    expect(nodeStatus(state, "verify_candidate")).toBe("unconfirmed");
  });
  it("shows the recorded span before a run finishes", () => {
    const state = applyEvents(emptyState(), [event(1), event(3)]);
    expect(summarize(state).elapsed).toBeNull();
    expect(summarize(state).recordedSpan).toBe(2);
  });
  it("rejects events from another run and never mixes child usage", () => {
    const state = applyEvents(emptyState(), [
      event(1),
      event(2, { run_id: "b".repeat(32) }),
    ]);
    expect(state.events).toHaveLength(1);
  });
  it("counts verified checkpoints per recipe step without counting retry generations twice", () => {
    const state = applyEvents(emptyState(), [
      event(1, { phase: "checkpoint_step", lifecycle: "completed", step: 1 }),
      event(2, {
        phase: "checkpoint_step",
        lifecycle: "completed",
        step: 1,
        generation: 1,
      }),
      event(3, { phase: "checkpoint_step", lifecycle: "completed", step: 2 }),
    ]);
    expect(summarize(state).recipes).toBe(2);
  });
  it("preserves terminal wall duration and observed route evidence", () => {
    const state = applyEvents(emptyState(), [
      event(1),
      event(2, { lifecycle: "completed" }),
      event(3, { phase: "finish_run", execution_id: "finish" }),
      event(4, {
        phase: "finish_run",
        execution_id: "finish",
        lifecycle: "completed",
        outcome: "completed",
      }),
    ]);
    expect(summarize(state).elapsed).toBe(3);
    expect(state.transitions).toEqual(["verify_candidate→finish_run"]);
  });
});

it("aggregates profile recipe totals from distinct child runs", () => {
  const parent = applyEvents(emptyState(), [event(1, { phase: "migrate" })]);
  const child = { run_id: "b".repeat(32), verified_recipes: 3 };
  expect(
    summarize(parent, [
      child,
      child,
      { run_id: "c".repeat(32), verified_recipes: 2 },
    ]).recipes,
  ).toBe(5);
});

it("totals local and child tokens without altering visits or counting replays", () => {
  const usage = event(2, {
    lifecycle: "usage",
    input_tokens: 13,
    output_tokens: 5,
  });
  const state = applyEvents(emptyState(), [event(1), usage, usage]);
  const child = { run_id: "b".repeat(32), input_tokens: 7, output_tokens: 2 };
  expect(summarize(state, [child, child])).toMatchObject({
    tokens: 27,
    inputTokens: 20,
    outputTokens: 7,
  });
  expect(nodeStatus(state, "verify_candidate")).toBe("unconfirmed");
});

it("shows planned names and the active child migration without mixing parent visits", () => {
  const planned = [
    {
      recipe: "org.example.SpringFoxToSpringDoc",
      name: "Spring Fox → Spring Doc",
    },
    { recipe: "org.example.UpgradeToJava17", name: "Upgrade to Java 17" },
  ];
  const state = applyEvents(emptyState(), [
    event(1, {
      phase: "plan",
      execution_id: "plan",
      lifecycle: "completed",
      migrations: planned,
    }),
    event(2, {
      phase: "migrate",
      execution_id: "current",
      migrations: planned,
    }),
  ]);
  expect(nodeMigrationContext(state, "plan")).toEqual({
    caption: "2 migrations planned",
    name: null,
  });
  expect(
    nodeMigrationContext(state, "migrate", [
      {
        parent_execution_id: "old",
        migration_name: "Old migration",
        step: 4,
        total_steps: 4,
      },
      {
        parent_execution_id: "current",
        migration_name: "Spring Fox → Spring Doc",
        step: 1,
        total_steps: 2,
      },
    ]),
  ).toEqual({ caption: "Migration 1/2", name: "Spring Fox → Spring Doc" });
  expect(state.visits.plan.migrations).toEqual(planned);
});

it("shows recipe names on execution nodes and tolerates legacy records", () => {
  const state = applyEvents(emptyState(), [
    event(1, {
      phase: "execute_recipe",
      migration_name: "Spring Fox → Spring Doc",
      step: 1,
      total_steps: 4,
    }),
  ]);
  expect(nodeMigrationContext(state, "execute_recipe")).toEqual({
    caption: "Migration 1/4",
    name: "Spring Fox → Spring Doc",
  });
  expect(nodeMigrationContext(emptyState(), "plan")).toEqual({
    caption: null,
    name: null,
  });
});

it("omits execution status for the graph boundaries only", () => {
  const state = emptyState();
  expect(nodeStatus(state, "__start__")).toBeNull();
  expect(nodeStatus(state, "__end__")).toBeNull();
  expect(nodeStatus(state, "plan")).toBe("not visited");
});

it("totals recorded cost and flags unpriced usage, including child runs", () => {
  const priced = event(1, {
    lifecycle: "usage",
    input_tokens: 20,
    estimated_cost_usd: 0.003,
  });
  const unpriced = event(2, { lifecycle: "usage", input_tokens: 20 });
  const child = {
    run_id: "child",
    estimated_cost_usd: 0.005,
    unpriced_usage: 0,
  };
  const state = applyEvents(emptyState(), [priced, unpriced, priced]);
  expect(summarize(state, [child, child])).toMatchObject({
    cost: 0.008,
    costPartial: true,
  });
  expect(summarize(applyEvents(emptyState(), [unpriced])).cost).toBeNull();
});
