export interface MonitorEvent {
  schema_version: 1;
  run_id: string;
  sequence: number;
  timestamp: string;
  event_id: string;
  execution_id: string;
  phase: string;
  lifecycle: "started" | "completed" | "failed" | "cancelled" | "interrupted";
  generation: number;
  outcome?: string | null;
  duration_seconds?: number | null;
  step?: number | null;
  recipe?: string | null;
  repair_stage?: string | null;
  repair_attempt?: number | null;
  java?: number | null;
  checkpoint?: string | null;
  parent_run_id?: string | null;
  parent_execution_id?: string | null;
  profile?: string | null;
}
export interface Topology {
  kind: "profile" | "migration";
  version: string;
  historical: boolean;
  nodes: { id: string; label: string }[];
  edges: { source: string; target: string; conditional: boolean }[];
}
export interface Run {
  run_id: string;
  kind: "profile" | "migration";
  profile: string | null;
  parent_run_id: string | null;
  parent_execution_id: string | null;
  status: string;
  first_seen: string | null;
  last_seen: string | null;
  event_count: number;
  verified_recipes?: number;
  diagnostics: string[];
}
export interface Snapshot {
  run: Run;
  events: MonitorEvent[];
  topology: Topology;
  cursor: number;
  diagnostics: string[];
}
export interface State {
  runId: string | null;
  events: MonitorEvent[];
  visits: Record<string, MonitorEvent>;
  cursor: number;
  transitions: string[];
}
export const emptyState = (): State => ({
  runId: null,
  events: [],
  visits: {},
  cursor: 0,
  transitions: [],
});
export function applyEvents(state: State, events: MonitorEvent[]): State {
  const next: State = {
    ...state,
    events: [...state.events],
    visits: { ...state.visits },
    transitions: [...state.transitions],
  };
  for (const event of events) {
    if (next.runId && event.run_id !== next.runId) continue;
    if (
      event.sequence <= next.cursor ||
      next.events.some((e) => e.event_id === event.event_id)
    )
      continue;
    // Only adjacent, completed→started records in one generation establish a route.
    const previous = next.events.at(-1);
    if (
      previous?.lifecycle === "completed" &&
      event.lifecycle === "started" &&
      previous.generation === event.generation &&
      previous.phase !== event.phase &&
      previous.phase !== "run"
    ) {
      const route = `${previous.phase}→${event.phase}`;
      if (!next.transitions.includes(route)) next.transitions.push(route);
    }
    next.runId = event.run_id;
    next.cursor = event.sequence;
    next.events.push(event);
    next.visits[event.execution_id] = {
      ...next.visits[event.execution_id],
      ...event,
    };
  }
  return next;
}
export function nodeStatus(state: State, phase: string): string {
  const event = Object.values(state.visits)
    .filter((v) => v.phase === phase)
    .sort((a, b) => b.sequence - a.sequence)[0];
  if (!event) return "not visited";
  if (event.lifecycle === "started") return "unconfirmed";
  if (event.lifecycle === "completed")
    return event.outcome?.includes("failed") ? "needs attention" : "completed";
  return event.lifecycle;
}
export function summarize(
  state: State,
  children: { run_id: string; verified_recipes?: number }[] = [],
) {
  const terminal = [...state.events]
    .reverse()
    .find(
      (e) =>
        (e.phase === "finish_run" || e.phase === "run") &&
        e.lifecycle === "completed",
    );
  const first = state.events[0];
  const last = state.events.at(-1);
  const recordedSpan =
    first && last
      ? Math.max(
          0,
          (Date.parse(last.timestamp) - Date.parse(first.timestamp)) / 1000,
        )
      : null;
  const elapsed =
    terminal && first
      ? Math.max(
          0,
          (Date.parse(terminal.timestamp) - Date.parse(first.timestamp)) / 1000,
        )
      : null;
  return {
    tokens: null,
    cost: null,
    elapsed,
    recordedSpan,
    recipes: children.length
      ? [
          ...new Map(children.map((child) => [child.run_id, child])).values(),
        ].reduce((total, child) => total + (child.verified_recipes ?? 0), 0)
      : new Set(
          state.events
            .filter(
              (e) =>
                e.phase === "checkpoint_step" &&
                e.lifecycle === "completed" &&
                e.step,
            )
            .map((e) => e.step),
        ).size,
    errors: state.events.filter((e) => e.lifecycle === "failed").length,
    status:
      terminal?.outcome ??
      (state.events.at(-1)?.lifecycle === "failed" ? "failed" : "unknown"),
  };
}
export const label = (value: string) =>
  value.replaceAll("_", " ").replaceAll("__", "").trim();
export const duration = (seconds: number | null | undefined) =>
  seconds == null
    ? "Unavailable"
    : seconds < 60
      ? `${seconds.toFixed(1)}s`
      : `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
export const shortId = (id: string) => id.slice(0, 8);
