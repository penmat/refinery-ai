// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import App from "./App";
import type { Snapshot, Run } from "./state";
vi.mock("./WorkflowGraph", () => ({ WorkflowGraph: () => null }));
class Stream {
  onopen = null;
  onerror = null;
  onmessage = null;
  addEventListener() {}
  close() {}
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("ignores an old snapshot after the user switches runs", async () => {
  const a = "a".repeat(32),
    b = "b".repeat(32);
  const run = (id: string, profile: string): Run => ({
    run_id: id,
    profile,
    kind: "profile",
    parent_run_id: null,
    parent_execution_id: null,
    status: "unknown",
    first_seen: null,
    last_seen: null,
    event_count: 0,
    diagnostics: [],
  });
  const runs = [
    { ...run(a, "Profile A"), parent_run_id: b, verified_recipes: 3 },
    run(b, "Profile B"),
  ];
  const snapshot = (run: Run): Snapshot => ({
    run,
    events: [],
    cursor: 0,
    diagnostics: [],
    topology: {
      kind: "profile",
      version: "1",
      historical: true,
      nodes: [],
      edges: [],
    },
  });
  let resolveA!: (value: unknown) => void;
  const old = new Promise((resolve) => {
    resolveA = resolve;
  });
  vi.stubGlobal("EventSource", Stream);
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) =>
      path === "/api/runs"
        ? Promise.resolve({
            ok: true,
            json: async () => ({ runs, diagnostics: [] }),
          })
        : path.endsWith(a)
          ? old
          : Promise.resolve({ ok: true, json: async () => snapshot(runs[1]) }),
    ),
  );
  render(<App />);
  await screen.findByRole("button", { name: /Profile B/ });
  fireEvent.click(screen.getByRole("button", { name: /Profile B/ }));
  await screen.findByRole("heading", { name: "Profile B" });
  resolveA({ ok: true, json: async () => snapshot(runs[0]) });
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Profile A" })).toBeNull(),
  );
  expect(screen.getByRole("heading", { name: "Profile B" })).toBeTruthy();
  expect(screen.getByText("3")).toBeTruthy();
});

it("lists the ordered migrations when the plan visit is opened", async () => {
  const run: Run = {
    run_id: "a".repeat(32),
    profile: "Profile",
    kind: "profile",
    parent_run_id: null,
    parent_execution_id: null,
    status: "unknown",
    first_seen: null,
    last_seen: null,
    event_count: 1,
    diagnostics: [],
  };
  const snapshot: Snapshot = {
    run,
    cursor: 1,
    diagnostics: [],
    topology: {
      kind: "profile",
      version: "1",
      historical: true,
      nodes: [],
      edges: [],
    },
    events: [
      {
        schema_version: 1,
        run_id: run.run_id,
        sequence: 1,
        event_id: "plan",
        execution_id: "planned",
        phase: "plan",
        lifecycle: "completed",
        generation: 0,
        timestamp: "2026-11-06T11:00:00Z",
        migrations: [
          {
            recipe: "org.example.SpringFoxToSpringDoc",
            name: "Spring Fox → Spring Doc",
          },
          { recipe: "org.example.UpgradeToJava17", name: "Upgrade to Java 17" },
        ],
      },
    ],
  };
  vi.stubGlobal("EventSource", Stream);
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) =>
      Promise.resolve({
        ok: true,
        json: async () =>
          path === "/api/runs" ? { runs: [run], diagnostics: [] } : snapshot,
      }),
    ),
  );
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: /plan.*completed/ }),
  );
  expect(screen.getByText("2 migrations planned")).toBeTruthy();
  const items = screen.getAllByRole("listitem");
  expect(items.map((item) => item.textContent)).toEqual([
    "Spring Fox → Spring Docorg.example.SpringFoxToSpringDoc",
    "Upgrade to Java 17org.example.UpgradeToJava17",
  ]);
});

it("shows discovery facts and only the recipe from the latest migrate visit", async () => {
  const root: Run = {
    run_id: "a".repeat(32),
    kind: "profile",
    profile: "Profile",
    parent_run_id: null,
    parent_execution_id: null,
    status: "unknown",
    first_seen: null,
    last_seen: null,
    event_count: 3,
    diagnostics: [],
  };
  const old = {
    ...root,
    run_id: "b".repeat(32),
    kind: "migration" as const,
    parent_run_id: root.run_id,
    parent_execution_id: "old",
    migration_name: "Old recipe",
  };
  const child = {
    ...old,
    run_id: "c".repeat(32),
    parent_execution_id: "current",
    migration_name: "Spring Fox → Spring Doc",
    recipe: "example.SpringFoxToSpringDoc",
    step: 1,
    total_steps: 4,
    step_status: "failed",
    failure_reason: "Migration command failed",
  };
  const event = (sequence: number, phase: string, execution_id: string) => ({
    schema_version: 1 as const,
    run_id: root.run_id,
    sequence,
    timestamp: "2026-11-06T11:00:00Z",
    event_id: execution_id,
    execution_id,
    phase,
    lifecycle: "completed" as const,
    generation: 0,
  });
  const snapshot = {
    run: root,
    cursor: 3,
    diagnostics: [],
    topology: {
      kind: "profile",
      version: "1",
      historical: true,
      nodes: [],
      edges: [],
    },
    events: [
      {
        ...event(1, "analyze", "analysis"),
        analysis: {
          java_version: "11",
          spring_boot_version: "2.7.18",
          build_system: "maven",
          frameworks: ["Spring MVC"],
        },
      },
      event(2, "migrate", "old"),
      {
        ...event(3, "migrate", "current"),
        migrations: [
          { name: "Another planned recipe", recipe: "example.Other" },
        ],
      },
    ],
  };
  vi.stubGlobal("EventSource", Stream);
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) =>
      Promise.resolve({
        ok: true,
        json: async () =>
          path === "/api/runs"
            ? { runs: [root, old, child], diagnostics: [] }
            : snapshot,
      }),
    ),
  );
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: /analyze.*completed/ }),
  );
  expect(screen.getByText("11")).toBeTruthy();
  expect(screen.getByText("2.7.18")).toBeTruthy();
  expect(screen.getByText("Spring MVC")).toBeTruthy();
  fireEvent.click(
    screen.getAllByRole("button", { name: /migrate.*completed/ })[0],
  );
  expect(screen.getByText("Spring Fox → Spring Doc")).toBeTruthy();
  expect(screen.getByText("example.SpringFoxToSpringDoc")).toBeTruthy();
  expect(screen.getByText("Migration command failed")).toBeTruthy();
  expect(screen.queryByText("Old recipe")).toBeNull();
  expect(screen.queryByText("Another planned recipe")).toBeNull();
});
