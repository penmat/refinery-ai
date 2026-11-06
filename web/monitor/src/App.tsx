import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronRight,
  Circle,
  Clock3,
  Coins,
  GitBranch,
  Layers3,
  PanelLeftClose,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
  AlertCircle,
} from "lucide-react";
import { getSnapshot, listRuns } from "./api";
import {
  applyEvents,
  emptyState,
  summarize,
  label,
  duration,
  shortId,
  type Run,
  type Snapshot,
  type State,
  type MonitorEvent,
} from "./state";
import { WorkflowGraph } from "./WorkflowGraph";

export default function App() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [state, setState] = useState<State>(emptyState);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [connection, setConnection] = useState("Connecting");
  const [error, setError] = useState<string | null>(null);
  const [discoveryErrors, setDiscoveryErrors] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const data = await listRuns(controller.signal);
        if (controller.signal.aborted) return;
        setRuns(data.runs);
        setDiscoveryErrors(data.diagnostics);
        setSelectedRun((id) => id ?? data.runs[0]?.run_id ?? null);
      } catch (e) {
        if (!controller.signal.aborted) setError(String(e));
      } finally {
        busy = false;
      }
    };
    void load();
    const timer = setInterval(() => void load(), 4000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    if (!selectedRun) return;
    const controller = new AbortController();
    let source: EventSource | null = null;
    setSnapshot(null);
    setState(emptyState());
    setSelectedNode(null);
    setError(null);
    setConnection("Connecting");
    void getSnapshot(selectedRun, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setSnapshot(data);
        setState(applyEvents(emptyState(), data.events));
        source = new EventSource(
          `/api/runs/${selectedRun}/events?after=${data.cursor}`,
        );
        source.onopen = () => {
          if (!controller.signal.aborted) setConnection("Connected");
        };
        source.onerror = () => {
          if (!controller.signal.aborted) setConnection("Reconnecting");
        };
        source.onmessage = (message) => {
          if (controller.signal.aborted) return;
          try {
            const event = JSON.parse(message.data) as MonitorEvent;
            setState((current) => applyEvents(current, [event]));
          } catch {
            setError("An update could not be read. Reload this run.");
          }
        };
        source.addEventListener("diagnostic", (message) => {
          if (controller.signal.aborted) return;
          setError(JSON.parse((message as MessageEvent).data).message);
          setConnection("Needs reload");
          source?.close();
        });
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError(String(e));
          setConnection("Disconnected");
        }
      });
    return () => {
      controller.abort();
      source?.close();
    };
  }, [selectedRun, reload]);
  const summary = summarize(
    state,
    runs.filter((run) => run.parent_run_id === selectedRun),
  );
  const current = snapshot?.run;
  const children = useMemo(
    () => runs.filter((r) => r.parent_run_id === selectedRun),
    [runs, selectedRun],
  );
  const childrenByNode = useMemo(() => {
    const counts: Record<string, number> = {};
    children.forEach((child) => {
      const event = state.events.find(
        (e) => e.execution_id === child.parent_execution_id,
      );
      if (event) counts[event.phase] = (counts[event.phase] ?? 0) + 1;
    });
    return counts;
  }, [children, state]);
  const visits = Object.values(state.visits)
    .filter((v) => v.phase === selectedNode)
    .sort((a, b) => b.sequence - a.sequence);
  const selectedChildren = children.filter((child) =>
    visits.some((v) => v.execution_id === child.parent_execution_id),
  );
  const showTime = (value: string | null | undefined) =>
    value
      ? new Date(value).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })
      : "—";
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/">
          <span className="brand-mark">
            <Layers3 size={21} />
          </span>
          refinery<span className="brand-ai">AI</span>
        </a>
        <div className="workspace-label">
          WORKSPACE <span>LOCAL</span>
        </div>
        <div className="nav-current">
          <Activity size={16} />
          Run monitor<span className="count">{runs.length}</span>
        </div>
        <div className="sidebar-heading">
          RUN HISTORY{" "}
          <button
            title="Refresh selected run"
            onClick={() => setReload((n) => n + 1)}
          >
            <RefreshCw size={13} />
          </button>
        </div>
        <label className="search">
          <Search size={14} />
          <input
            aria-label="Filter runs"
            placeholder="Find a run…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </label>
        <div className="run-list">
          {runs
            .filter((r) =>
              `${r.profile ?? ""} ${r.run_id} ${r.kind}`
                .toLowerCase()
                .includes(filter.toLowerCase()),
            )
            .map((run) => (
              <button
                key={run.run_id}
                className={`run-item ${selectedRun === run.run_id ? "selected" : ""}`}
                onClick={() => setSelectedRun(run.run_id)}
              >
                <div className="run-title">
                  <span
                    className={`run-dot ${run.status === "completed" ? "done" : run.status === "failed" ? "failed" : ""}`}
                  />
                  <strong>
                    {run.profile ??
                      (run.kind === "migration" ? "Migration" : "Profile run")}
                  </strong>
                </div>
                <div className="run-item-meta">
                  <span>{shortId(run.run_id)}</span>
                  <span>{run.kind}</span>
                </div>
                <div className="run-date">
                  {run.first_seen
                    ? new Date(run.first_seen).toLocaleString([], {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : "No recorded events"}
                </div>
              </button>
            ))}
        </div>
        <div className="sidebar-bottom">
          <ShieldCheck size={16} />
          <div>
            Read-only monitoring<small>Your runs continue independently.</small>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            Workspace
            <ChevronRight size={13} />
            <strong>Run monitor</strong>
          </div>
          <span
            className={`connection ${connection === "Connected" ? "connected" : ""}`}
          >
            <span />
            {selectedRun ? connection : "Local dashboard"}
          </span>
        </header>
        <div className="page-content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">EXECUTION OBSERVATORY</div>
              <h1>Every step, in view.</h1>
              <p>Follow the workflow. Understand the outcome.</p>
            </div>
            <div className="local-badge">
              <Circle size={10} /> Local workspace
            </div>
          </div>
          {discoveryErrors.map((text, i) => (
            <div className="error-banner" key={i}>
              <AlertCircle size={16} />
              {text}
            </div>
          ))}
          {error && (
            <div className="error-banner">
              <AlertCircle size={16} />
              {error}
              <button onClick={() => setReload((n) => n + 1)}>Reload</button>
            </div>
          )}
          {!selectedRun ? (
            <div className="empty-state">
              <GitBranch size={42} />
              <h2>Ready when your next run starts.</h2>
              <p>
                Run Refinery in this repository, then return here to follow its
                progress.
                <br />
                Saved profile and migration runs appear automatically.
              </p>
              <code>refinery monitor</code>
              <small>
                The dashboard reads your existing .refinery journals.
              </small>
            </div>
          ) : !snapshot ? (
            <div className="empty-state">
              <RefreshCw size={26} />
              <h2>Loading run history…</h2>
            </div>
          ) : (
            <>
              {current?.parent_run_id && (
                <button
                  className="back-link"
                  onClick={() => setSelectedRun(current.parent_run_id)}
                >
                  <ArrowLeft size={13} /> Back to parent workflow
                </button>
              )}
              <div className="run-heading">
                <div>
                  <span className="kind-tag">
                    {current?.kind === "profile"
                      ? "PROFILE WORKFLOW"
                      : "MIGRATION"}
                  </span>
                  <h2>{current?.profile ?? "Migration execution"}</h2>
                  <div className="run-subtitle">
                    <code>{shortId(selectedRun)}</code>
                    <span>
                      Started{" "}
                      {current?.first_seen
                        ? new Date(current.first_seen).toLocaleString()
                        : "—"}
                    </span>
                  </div>
                </div>
                <span
                  className={`outcome-pill ${summary.status === "completed" ? "good" : ""}`}
                >
                  {summary.status === "completed" ? (
                    <Check size={13} />
                  ) : (
                    <Circle size={11} />
                  )}{" "}
                  {summary.status === "unknown"
                    ? "Outcome unconfirmed"
                    : label(summary.status)}
                </span>
              </div>
              <div className="metrics">
                <Metric
                  icon={<Clock3 size={16} />}
                  label={
                    summary.elapsed === null ? "Recorded time" : "Wall time"
                  }
                  value={duration(summary.elapsed ?? summary.recordedSpan)}
                  note={
                    summary.elapsed === null
                      ? "First to latest recorded event"
                      : "Start to recorded completion"
                  }
                />
                <Metric
                  icon={<Layers3 size={16} />}
                  label="Verified recipes"
                  value={String(summary.recipes)}
                  note={
                    current?.kind === "profile"
                      ? "Verified steps across child migrations"
                      : "Unique checkpointed steps"
                  }
                />
                <Metric
                  icon={<Sparkles size={16} />}
                  label="Recorded tokens"
                  value={summary.tokens?.toLocaleString() ?? "Unavailable"}
                  note={
                    summary.tokens === null
                      ? "No token usage recorded"
                      : `${summary.inputTokens?.toLocaleString() ?? "—"} input · ${summary.outputTokens?.toLocaleString() ?? "—"} output`
                  }
                />
                <Metric
                  icon={<Coins size={16} />}
                  label="Estimated cost"
                  value="Unavailable"
                  note="Pricing data is not recorded"
                />
                <Metric
                  icon={<AlertCircle size={16} />}
                  label="Execution errors"
                  value={String(summary.errors)}
                  note="Exceptions across all attempts"
                />
              </div>
              {snapshot.diagnostics.map((text, i) => (
                <div className="error-banner" key={i}>
                  <AlertCircle size={14} />
                  {text}
                </div>
              ))}
              <div
                className={`graph-layout ${selectedNode ? "with-details" : ""}`}
              >
                <section className="panel graph-panel">
                  <div className="panel-heading">
                    <div>
                      <GitBranch size={17} />
                      <h3>Workflow graph</h3>
                      <span className="subtle">
                        {snapshot.topology.nodes.length} nodes
                      </span>
                    </div>
                    <span className="topology-label">
                      {snapshot.topology.historical
                        ? "Recorded definition"
                        : "Current definition · historical topology unavailable"}
                    </span>
                  </div>
                  <div className="graph-canvas">
                    <WorkflowGraph
                      key={selectedRun}
                      topology={snapshot.topology}
                      state={state}
                      selected={selectedNode}
                      onSelect={setSelectedNode}
                      childrenByNode={childrenByNode}
                    />
                  </div>
                  <div className="graph-footer">
                    <span>
                      <i className="legend-dot complete" />
                      Completed
                    </span>
                    <span>
                      <i className="legend-dot pending" />
                      Started, unconfirmed
                    </span>
                    <span>
                      <i className="legend-dot failure" />
                      Failed / attention
                    </span>
                    <span className="legend-note">
                      Solid green edges show observed routes
                    </span>
                  </div>
                </section>
                {selectedNode && (
                  <aside className="panel detail-panel">
                    <div className="panel-heading">
                      <h3>Step details</h3>
                      <button
                        aria-label="Close details"
                        onClick={() => setSelectedNode(null)}
                      >
                        <X size={15} />
                      </button>
                    </div>
                    <div className="detail-content">
                      <span className="eyebrow">WORKFLOW NODE</span>
                      <h2>{label(selectedNode)}</h2>
                      <p className="subtle">
                        {visits.length} recorded{" "}
                        {visits.length === 1 ? "visit" : "visits"}
                      </p>
                      {!visits.length && (
                        <p className="detail-note">
                          This node has no recorded execution.
                        </p>
                      )}
                      {selectedChildren.map((child) => (
                        <button
                          key={child.run_id}
                          className="child-link"
                          onClick={() => setSelectedRun(child.run_id)}
                        >
                          <GitBranch size={15} />
                          <span>
                            Open migration<small>{shortId(child.run_id)}</small>
                          </span>
                          <ArrowUpRight size={15} />
                        </button>
                      ))}
                      {visits.map((visit, i) => (
                        <div className="visit" key={visit.execution_id}>
                          <div className="visit-title">
                            <strong>Visit {visits.length - i}</strong>
                            <span>
                              {visit.lifecycle === "started"
                                ? "Unconfirmed"
                                : label(visit.lifecycle)}
                            </span>
                          </div>
                          {visit.lifecycle === "started" && (
                            <p className="detail-note">
                              A start was recorded. Worker liveness is unknown.
                            </p>
                          )}
                          <dl>
                            <dt>Duration</dt>
                            <dd>{duration(visit.duration_seconds)}</dd>
                            <dt>Outcome</dt>
                            <dd>
                              {visit.outcome
                                ? label(visit.outcome)
                                : "Not recorded"}
                            </dd>
                            <dt>Generation</dt>
                            <dd>{visit.generation}</dd>
                            {visit.step != null && (
                              <>
                                <dt>Recipe step</dt>
                                <dd>{visit.step}</dd>
                              </>
                            )}
                            {visit.repair_attempt != null && (
                              <>
                                <dt>Repair attempt</dt>
                                <dd>{visit.repair_attempt}</dd>
                              </>
                            )}
                            {visit.java && (
                              <>
                                <dt>JDK</dt>
                                <dd>{visit.java}</dd>
                              </>
                            )}
                          </dl>
                          {visit.recipe && (
                            <code className="recipe-name">{visit.recipe}</code>
                          )}
                          {visit.checkpoint && (
                            <p className="checkpoint">
                              Checkpoint{" "}
                              <code>{visit.checkpoint.slice(0, 12)}</code>
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  </aside>
                )}
              </div>
              {children.length > 0 && (
                <div className="child-strip">
                  <span>CHILD MIGRATIONS</span>
                  {children.map((child) => (
                    <button
                      key={child.run_id}
                      onClick={() => setSelectedRun(child.run_id)}
                    >
                      <GitBranch size={13} />
                      {shortId(child.run_id)}
                      <ArrowUpRight size={13} />
                    </button>
                  ))}
                </div>
              )}
              <section className="panel activity-panel">
                <div className="panel-heading">
                  <div>
                    <Activity size={17} />
                    <h3>Activity</h3>
                    <span className="subtle">{state.events.length} events</span>
                  </div>
                  <span className="subtle">Newest first</span>
                </div>
                <div className="activity-table">
                  <div className="activity-row table-head">
                    <span>TIME</span>
                    <span>STEP</span>
                    <span>EVENT / OUTCOME</span>
                    <span>DURATION</span>
                  </div>
                  {[...state.events]
                    .reverse()
                    .slice(0, 100)
                    .map((event) => (
                      <button
                        key={event.event_id}
                        className="activity-row"
                        onClick={() => setSelectedNode(event.phase)}
                      >
                        <span className="event-time">
                          {showTime(event.timestamp)}
                        </span>
                        <span className="event-phase">
                          <i
                            className={`legend-dot ${event.lifecycle === "completed" ? "complete" : event.lifecycle === "started" ? "pending" : event.lifecycle === "usage" ? "" : "failure"}`}
                          />
                          {label(event.phase)}
                          {event.repair_attempt ? (
                            <small>#{event.repair_attempt}</small>
                          ) : null}
                        </span>
                        <span className="event-outcome">
                          {label(event.lifecycle)}
                          {event.outcome && <> · {label(event.outcome)}</>}
                        </span>
                        <span className="event-time">
                          {event.duration_seconds == null
                            ? "—"
                            : duration(event.duration_seconds)}
                        </span>
                      </button>
                    ))}
                  {state.events.length === 0 && (
                    <p className="detail-note">
                      Waiting for the first complete event.
                    </p>
                  )}
                </div>
                {state.events.length > 100 && (
                  <div className="table-note">
                    Showing the latest 100 events. All visits remain available
                    in step details.
                  </div>
                )}
              </section>
            </>
          )}
          <footer className="page-footer">
            <span>REFINERY AI</span>
            <span>Local history. Live perspective.</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
function Metric({
  icon,
  label,
  value,
  note,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  note: string;
}) {
  return (
    <div className="metric">
      <div className="metric-label">
        {icon}
        {label}
      </div>
      <div
        className={`metric-value ${value === "Unavailable" ? "unavailable" : ""}`}
      >
        {value}
      </div>
      <div className="metric-note">{note}</div>
    </div>
  );
}
