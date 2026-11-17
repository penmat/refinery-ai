// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WorkflowGraph } from "./WorkflowGraph";
import { applyEvents, emptyState, type Run } from "./state";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("connects child history to the graph and preserves keyboard node inspection", () => {
  // JSDOM has no layout observer; graph data/rendering remains the real React Flow path.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const state = applyEvents(emptyState(), [
    {
      schema_version: 1,
      run_id: "parent",
      event_id: "event",
      execution_id: "visit",
      sequence: 1,
      timestamp: "2026-11-17T00:00:00Z",
      phase: "migrate",
      lifecycle: "started",
      generation: 0,
    },
  ]);
  const child: Run = {
    run_id: "child",
    kind: "migration",
    profile: null,
    parent_run_id: "parent",
    parent_execution_id: "visit",
    status: "unknown",
    first_seen: null,
    last_seen: null,
    event_count: 1,
    diagnostics: [],
    execution_history: [
      {
        id: "attempt",
        node: "migrate",
        run_id: "child",
        parent_execution_id: "visit",
        generation: 0,
        step: 1,
        recipe: "Java17",
        label: "Java 17 compatibility",
        attempt: 1,
        status: "verified",
      },
    ],
  };
  const select = vi.fn();
  render(
    <WorkflowGraph
      topology={{
        kind: "profile",
        version: "1",
        historical: true,
        nodes: [{ id: "migrate", label: "Migration" }],
        edges: [],
      }}
      state={state}
      selected={null}
      onSelect={select}
      childrenByNode={{ migrate: 1 }}
      migrationRuns={[child]}
    />,
  );
  expect(screen.getByText("Java 17 compatibility")).toBeTruthy();
  expect(screen.getByText("Build/tests verified")).toBeTruthy();
  fireEvent.keyDown(
    screen.getByLabelText("Inspect migrate"),
    { key: "Enter" },
  );
  expect(select).toHaveBeenCalledWith("migrate");
});
