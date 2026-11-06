import { describe, it, expect } from "vitest";
import {
  applyEvents,
  emptyState,
  summarize,
  nodeStatus,
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
