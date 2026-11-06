import type { Run, Snapshot } from "./state";
async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok)
    throw new Error(`Could not load dashboard data (${response.status}).`);
  return response.json() as Promise<T>;
}
export const listRuns = (signal?: AbortSignal) =>
  get<Run[]>("/api/runs", signal);
export const getSnapshot = (id: string, signal?: AbortSignal) =>
  get<Snapshot>(`/api/runs/${encodeURIComponent(id)}`, signal);
