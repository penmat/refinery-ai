export interface AnalysisSummary {
  java_version?: string | null;
  spring_boot_version?: string | null;
  build_system?: string | null;
  frameworks: string[];
}

export interface PlannedMigration {
  recipe: string;
  name: string;
}

export interface MonitorEvent {
  schema_version: 1;
  run_id: string;
  sequence: number;
  timestamp: string;
  event_id: string;
  execution_id: string;
  phase: string;
  lifecycle:
    "started" | "completed" | "failed" | "cancelled" | "interrupted" | "usage";
  generation: number;
  outcome?: string | null;
  analysis?: AnalysisSummary | null;
  child_run_id?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  duration_seconds?: number | null;
  step?: number | null;
  recipe?: string | null;
  migration_name?: string | null;
  total_steps?: number | null;
  migrations?: PlannedMigration[] | null;
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
  migration_name?: string | null;
  recipe?: string | null;
  step_status?: string | null;
  failure_reason?: string | null;
  step?: number | null;
  total_steps?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
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
    if (event.lifecycle === "usage") continue;
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
  children: Pick<
    Run,
    "run_id" | "verified_recipes" | "input_tokens" | "output_tokens"
  >[] = [],
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
  const uniqueChildren = [
    ...new Map(children.map((child) => [child.run_id, child])).values(),
  ];
  const usage = [...state.events, ...uniqueChildren];
  const total = (key: "input_tokens" | "output_tokens") => {
    const counts = usage
      .map((item) => item[key])
      .filter((count) => count != null);
    return counts.length ? counts.reduce((sum, count) => sum + count, 0) : null;
  };
  const inputTokens = total("input_tokens");
  const outputTokens = total("output_tokens");
  return {
    inputTokens,
    outputTokens,
    tokens:
      inputTokens === null && outputTokens === null
        ? null
        : (inputTokens ?? 0) + (outputTokens ?? 0),
    cost: null,
    elapsed,
    recordedSpan,
    recipes: children.length
      ? uniqueChildren.reduce(
          (total, child) => total + (child.verified_recipes ?? 0),
          0,
        )
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

export function nodeMigrationContext(
  state: State,
  phase: string,
  children: Partial<Run>[] = [],
): { caption: string | null; name: string | null } {
  const visit = Object.values(state.visits)
    .filter((event) => event.phase === phase)
    .sort((a, b) => b.sequence - a.sequence)[0];
  if (phase === "plan" && visit?.migrations != null) {
    const count = visit.migrations.length;
    return {
      caption:
        count === 0
          ? "No migrations planned"
          : `${count} migration${count === 1 ? "" : "s"} planned`,
      name: null,
    };
  }
  const child =
    phase === "migrate" ? latestMigrationChild(visit, children) : undefined;
  const progress = child?.migration_name ? child : visit;
  if (progress?.migration_name) {
    return {
      caption:
        progress.step != null
          ? `Migration ${progress.step}${progress.total_steps != null ? `/${progress.total_steps}` : ""}`
          : "Migration",
      name: progress.migration_name,
    };
  }
  if (phase === "migrate" && visit?.migrations != null) {
    const count = visit.migrations.length;
    return {
      caption: `${count} migration${count === 1 ? "" : "s"}${visit.lifecycle === "started" ? " to run" : ""}`,
      name: null,
    };
  }
  return { caption: null, name: null };
}

export function latestMigrationChild<T extends Partial<Run>>(
  visit: MonitorEvent | undefined,
  children: T[],
): T | undefined {
  if (!visit) return undefined;
  if (visit.child_run_id)
    return children.find((run) => run.run_id === visit.child_run_id);
  return children
    .filter((run) => run.parent_execution_id === visit.execution_id)
    .sort((a, b) => (b.last_seen ?? "").localeCompare(a.last_seen ?? ""))[0];
}
