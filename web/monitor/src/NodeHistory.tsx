import { useId, useState } from "react";
import {
  Check,
  Circle,
  AlertCircle,
  Minus,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import type { DisplayHistoryEntry } from "./executionHistory";
import type { ExecutionHistoryEntry } from "./state";

const statusLabels: Record<ExecutionHistoryEntry["status"], string> = {
  active: "Active · status unconfirmed",
  applied: "Changes applied · not yet verified",
  verified: "Build/tests verified",
  failed: "Failed",
  skipped: "Skipped",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
  previewed: "Preview completed · no changes applied",
  completed: "Completed · verification unknown",
};

export function NodeHistory({ entries }: { entries: DisplayHistoryEntry[] }) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  if (!entries.length)
    return <p className="node-history-empty">No migrations executed yet</p>;
  const counts = new Map<string, number>();
  entries.forEach(({ status }) =>
    counts.set(status, (counts.get(status) ?? 0) + 1),
  );
  const summary = [...counts]
    .map(([status, count]) => `${count} ${status}`)
    .join(" · ");
  const visible = expanded ? entries : entries.slice(-3);
  return (
    <section className="node-history" aria-label="Migration execution history">
      <p className="node-history-summary" title={summary}>
        {summary}
      </p>
      <ol
        id={listId}
        className="node-history-list nowheel nodrag"
        tabIndex={0}
        aria-label={
          expanded ? "All migration attempts" : "Latest migration attempts"
        }
      >
        {visible.map((entry) => {
          const Icon =
            entry.status === "verified"
              ? Check
              : entry.status === "failed"
                ? AlertCircle
                : entry.status === "skipped"
                  ? Minus
                  : Circle;
          return (
            <li key={entry.id} className={`history-${entry.status}`}>
              <Icon size={13} aria-hidden="true" />
              <div>
                <small>
                  {entry.cycle === null
                    ? "Cycle unavailable"
                    : `Cycle ${entry.cycle}`}{" "}
                  · Attempt {entry.attempt}
                </small>
                <strong title={entry.label}>{entry.label}</strong>
                <span>{statusLabels[entry.status]}</span>
              </div>
            </li>
          );
        })}
      </ol>
      {entries.length > 3 && (
        <button
          type="button"
          className="node-history-toggle nodrag"
          aria-expanded={expanded}
          aria-controls={listId}
          onKeyDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            setExpanded(!expanded);
          }}
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? "Show latest 3" : `Show all ${entries.length} attempts`}
        </button>
      )}
    </section>
  );
}
