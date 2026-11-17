// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { NodeHistory } from "./NodeHistory";
import type { ExecutionHistoryEntry } from "./state";

afterEach(cleanup);
const entries = ["verified", "failed", "skipped", "active"].map(
  (status, index) => ({
    id: `entry${index}`,
    node: "migrate",
    run_id: "run",
    generation: 0,
    step: index + 1,
    recipe: `recipe${index}`,
    label: `Migration ${index + 1}`,
    parent_execution_id: "parent",
    cycle: index < 3 ? 1 : 2,
    attempt: 1,
    status,
  }),
) as (ExecutionHistoryEntry & { cycle: number })[];

it("shows latest three, keeps full history available, and isolates expansion clicks", () => {
  const select = vi.fn();
  render(
    <div onClick={select} onKeyDown={select}>
      <NodeHistory entries={entries} />
    </div>,
  );
  expect(screen.queryByText("Migration 1")).toBeNull();
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
  expect(screen.getByText(/1 verified/)).toBeTruthy();
  const toggle = screen.getByRole("button", { name: /Show all 4/ });
  fireEvent.keyDown(toggle, { key: "Enter" });
  fireEvent.click(toggle);
  expect(select).not.toHaveBeenCalled();
  expect(screen.getAllByRole("listitem")).toHaveLength(4);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Migration 1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Show latest 3/ }));
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
});

it("distinguishes application from verification and discloses uncertain liveness", () => {
  render(
    <NodeHistory
      entries={[{ ...entries[0], status: "applied" }, entries[3]]}
    />,
  );
  expect(screen.getByText("Changes applied · not yet verified")).toBeTruthy();
  expect(screen.getByText("Active · status unconfirmed")).toBeTruthy();
  expect(screen.queryByText("Build/tests verified")).toBeNull();
  expect(screen.getByText(/Cycle 2/)).toBeTruthy();
});

it("renders an honest empty state and unknown cycle", () => {
  const { rerender } = render(<NodeHistory entries={[]} />);
  expect(screen.getByText("No migrations executed yet")).toBeTruthy();
  rerender(
    <NodeHistory
      entries={[{ ...entries[0], cycle: null, status: "completed" }]}
    />,
  );
  expect(screen.getByText(/Cycle unavailable/)).toBeTruthy();
  expect(screen.getByText("Completed · verification unknown")).toBeTruthy();
});
