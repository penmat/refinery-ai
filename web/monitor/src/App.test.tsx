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
  const runs = [run(a, "Profile A"), run(b, "Profile B")];
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
        ? Promise.resolve({ ok: true, json: async () => runs })
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
});
