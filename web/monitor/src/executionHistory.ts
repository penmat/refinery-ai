import type { ExecutionHistoryEntry, Run, State } from "./state";

export type DisplayHistoryEntry = ExecutionHistoryEntry & {
  cycle: number | null;
};

/** Combine durable child histories without collapsing repeated parent invocations. */
export function nodeExecutionHistory(
  state: State,
  phase: string,
  runs: Run[],
): DisplayHistoryEntry[] {
  if (phase !== "migrate" && phase !== "execute_recipe") return [];
  const standalone = phase === "execute_recipe";
  const visits = Object.values(state.visits)
    .filter((event) => event.phase === "migrate")
    .sort((a, b) => a.sequence - b.sequence);
  const entries = new Map<string, DisplayHistoryEntry>();
  for (const run of runs) {
    if (
      standalone
        ? run.run_id !== state.runId
        : run.parent_run_id !== state.runId
    )
      continue;
    for (const entry of run.execution_history ?? []) {
      const visitIndex = visits.findIndex(
        (visit) =>
          visit.execution_id === entry.parent_execution_id ||
          (!entry.parent_execution_id && visit.child_run_id === run.run_id),
      );
      entries.set(entry.id, {
        ...entry,
        cycle: standalone
          ? entry.generation + 1
          : visitIndex < 0
            ? null
            : visitIndex + 1,
      });
    }
  }
  // Server lists runs newest first. Parent order is the authoritative cycle order.
  const ordered = [...entries.values()].sort(
    (a, b) =>
      (a.cycle ?? Infinity) - (b.cycle ?? Infinity) ||
      a.generation - b.generation ||
      a.step - b.step,
  );
  const attempts = new Map<string, number>();
  return ordered.map((entry) => {
    const attempt = (attempts.get(entry.recipe) ?? 0) + 1;
    attempts.set(entry.recipe, attempt);
    return { ...entry, attempt };
  });
}
