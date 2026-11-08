// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { Scorecard } from "./Scorecard";
import type { AssessmentScore } from "./state";

afterEach(cleanup);
const score = (
  id: string,
  overall: number | null,
  fingerprint = "same",
): AssessmentScore => ({
  assessment_id: id,
  created_at: "2026-11-08T16:00:00Z",
  profile_fingerprint: fingerprint,
  completeness: 0.75,
  overall,
  categories: { dependencies: overall, build: null },
  deductions: [
    { category: "dependencies", points: 2, reason: "Outdated library" },
  ],
});
it("shows starting, latest and improvement and expands the category breakdown", () => {
  render(<Scorecard assessments={[score("first", 62), score("last", 78)]} />);
  expect(
    within(
      screen.getByRole("button", { name: /modernization score/i }),
    ).getByText("62/100"),
  ).toBeTruthy();
  expect(
    within(
      screen.getByRole("button", { name: /modernization score/i }),
    ).getByText("78/100"),
  ).toBeTruthy();
  expect(screen.getByText("+16 points")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /modernization score/i }));
  const table = screen.getByRole("table");
  expect(within(table).getByText("Dependencies")).toBeTruthy();
  expect(within(table).getAllByText("Not assessed")).toHaveLength(2);
  expect(screen.getByText("Outdated library")).toBeTruthy();
});
it("keeps zero and waits for a distinct second measurement", () => {
  const first = score("first", 0);
  render(<Scorecard assessments={[first, first]} />);
  expect(
    within(
      screen.getByRole("button", { name: /modernization score/i }),
    ).getByText("0/100"),
  ).toBeTruthy();
  expect(screen.getByText("Waiting for another assessment")).toBeTruthy();
});
it("withholds change when scores are incompatible", () => {
  render(
    <Scorecard
      assessments={[score("first", 62), score("last", 78, "changed")]}
    />,
  );
  expect(screen.getByText("Scoring profiles differ")).toBeTruthy();
  expect(screen.queryByText("+16 points")).toBeNull();
});
