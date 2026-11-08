import { useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { AssessmentScore } from "./state";

const number = (value: number) =>
  value.toLocaleString(undefined, { maximumFractionDigits: 1 });
const score = (value: number | null | undefined) =>
  value == null ? "Not assessed" : `${number(value)}/100`;
const signed = (value: number) => `${value > 0 ? "+" : ""}${number(value)}`;
const delta = (
  before: number | null | undefined,
  after: number | null | undefined,
) =>
  before == null || after == null
    ? null
    : Math.round((after - before) * 10) / 10;
const coverage = (value: number) => `${number(value * 100)}% assessed`;
const categoryName = (name: string) =>
  name.charAt(0).toUpperCase() + name.slice(1);

export function Scorecard({ assessments }: { assessments: AssessmentScore[] }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const unique = [
    ...new Map(assessments.map((item) => [item.assessment_id, item])).values(),
  ];
  const starting = unique[0];
  const latest = unique.length > 1 ? unique.at(-1) : undefined;
  if (!starting)
    return (
      <section className="scorecard scorecard-empty">
        <h3>Modernization score</h3>
        <p>No assessment scores recorded for this run.</p>
      </section>
    );
  const categories = [
    ...new Set([
      ...Object.keys(starting.categories),
      ...Object.keys(latest?.categories ?? {}),
    ]),
  ];
  const sameProfile =
    latest?.profile_fingerprint === starting.profile_fingerprint;
  const sameCategories =
    !!latest &&
    categories.every(
      (name) =>
        (starting.categories[name] != null) ===
        (latest.categories[name] != null),
    );
  const change =
    sameProfile && sameCategories
      ? delta(starting.overall, latest?.overall)
      : null;
  const note = !latest
    ? "Waiting for another assessment"
    : !sameProfile
      ? "Scoring profiles differ"
      : !sameCategories
        ? "Assessed categories differ"
        : change == null
          ? "Overall change unavailable"
          : change === 0
            ? "No change"
            : `${signed(change)} points`;
  const measured = latest ?? starting;
  return (
    <section className="scorecard">
      <button
        type="button"
        className="scorecard-toggle"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setOpen(!open)}
      >
        <span className="scorecard-title">
          <strong>Modernization score</strong>
          <small>
            Measured {new Date(measured.created_at).toLocaleString()}
          </small>
        </span>
        <span className="scorecard-reading">
          <small>Starting</small>
          <strong>{score(starting.overall)}</strong>
        </span>
        <span className="scorecard-reading">
          <small>Latest</small>
          <strong>{latest ? score(latest.overall) : "—"}</strong>
        </span>
        <span
          className={`scorecard-change ${change != null && change > 0 ? "gain" : change != null && change < 0 ? "loss" : ""}`}
        >
          {note}
        </span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      <div id={detailsId} className="scorecard-details" hidden={!open}>
        <table>
          <caption>Assessment breakdown</caption>
          <thead>
            <tr>
              <th scope="col">Category</th>
              <th scope="col">Starting</th>
              <th scope="col">Latest</th>
              <th scope="col">Change</th>
            </tr>
          </thead>
          <tbody>
            {categories.map((name) => {
              const before = starting.categories[name];
              const after = latest?.categories[name];
              const points = sameProfile ? delta(before, after) : null;
              return (
                <tr key={name}>
                  <th scope="row">{categoryName(name)}</th>
                  <td>{score(before)}</td>
                  <td>{latest ? score(after) : "—"}</td>
                  <td>{points == null ? "—" : signed(points)}</td>
                </tr>
              );
            })}
            <tr>
              <th scope="row">Coverage</th>
              <td>{coverage(starting.completeness)}</td>
              <td>{latest ? coverage(latest.completeness) : "—"}</td>
              <td>—</td>
            </tr>
          </tbody>
        </table>
        {measured.deductions.length > 0 && (
          <div className="scorecard-deductions">
            <h4>Largest remaining deductions</h4>
            <ul>
              {measured.deductions.map((item, index) => (
                <li key={index}>
                  <strong>
                    −{number(item.points)} points ·{" "}
                    {categoryName(item.category)}
                  </strong>{" "}
                  {item.reason}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
