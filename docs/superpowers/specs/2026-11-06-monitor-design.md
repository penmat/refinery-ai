# Refinery monitor dashboard

## Intent

Provide a bundled browser dashboard launched by `refinery monitor`, showing live
workflow execution and recorded history without requiring LangSmith or Grafana.
The user selected a React Flow dashboard backed by repository-local JSONL journals
and a local server that delivers snapshots and Server-Sent Events (SSE).

## Scope and defaults

Proposed first release: current repository by default, with an explicit repository
option and existing configured state directory (normally `.refinery`). Serve on
loopback only, open the browser automatically, and support a no-browser option and
configurable port. Closing the dashboard must not interrupt execution. Node.js is
needed to develop/build the frontend, but installed Refinery ships compiled assets
and requires only its Python runtime to serve them.

Include parent profile runs, child migration invocations, historical selection,
interactive graphs, elapsed time, recipe progress, activity, execution details,
and errors. Token usage and cost show unavailable until reliable attributed data
exists; never substitute zero. No workflow controls, remote hosting, authentication,
multi-repository aggregation, or required observability service in this release.
These scope limits are proposed defaults, not previously stated user requirements.

## Approaches

1. Bundled React Flow UI and a local Python server: selected in conversation;
   supports tailored run navigation and requires no external dashboard service.
2. Grafana: useful for metric dashboards, but requires deployment/data-source
   integration and more adaptation for migration and attempt navigation.
3. LangSmith Studio: useful complementary tooling, but does not meet the requested
   built-in Refinery experience.

## Screen and interaction

A run list selects a profile or standalone migration. Summary cards show status,
elapsed wall time, recipe progress, tokens and estimated cost when available.
The central graph shows workflow topology, active operations, completed operations,
and observed routes. Clicking a node opens execution details and individual visits;
an activity timeline preserves recipe and repair attempt order.

Profile `migrate` visits link to individual child migration runs. Use navigation
with breadcrumbs for the initial UI rather than expanding every child into one
large canvas. Preserve explicit parent-child relationships so inline expansion can
be added later. A standalone migration remains navigable without a parent.

Differentiate a completed verification reporting a failed build from a node that
raised an exception. Retain individual execution IDs, repair attempts, steps and
recovery generations; one node may execute many times. Keep graph layout stable
during live updates. Use text/icons alongside status colors.

## Data and persistence

Reuse existing `MigrationEvent` journals under
`.refinery/migration-events/<run_id>.jsonl`; do not introduce a competing migration
writer. Add profile lifecycle persistence under `profile-events/<run_id>.jsonl`
using the same envelope conventions. Correlate child migrations using parent run
ID and parent node-execution ID. Schema changes must remain compatible with
existing v1 migration journals; older runs without parent links display standalone.
Coordinate event integration with the migration work already underway.

Persist graph topology/version metadata for new runs so historical execution is
not falsely rendered against changed code. Derive topology from graph definitions
without running effects. Existing journals may use current topology, explicitly
labelled as such. Distinguish possible edges from observed transitions; do not
invent traversed edges when the journal lacks enough evidence.

Keep prompts, source text, secrets and raw model responses out of events. Artifact
references remain separate from full logs. No retention deletion or SQLite index
in the initial release. Repository deletion also deletes its local history.

## Server and live transport

The Python monitor server reads journals independently of the worker. Provide run
listing, run snapshot/topology, and per-run SSE routes. Snapshot includes a sequence
cursor; streaming starts after it, preventing gaps during initial load. SSE IDs and
Last-Event-ID support reconnect/replay. Deduplicate by run and sequence/event ID.

Tail incrementally with bounded batches and buffers; ignore an unfinished trailing
line until complete. Surface malformed complete records and unsupported versions
as a run diagnostic. Handle disconnects and slow consumers without blocking worker
execution. Monitor failures never alter migration state or journal files.

The frontend reduces events into view state; React Flow receives nodes and edges,
while other panels use the same reduced state. No log scraping or LangSmith API
dependency. A disconnected stream is a monitor connection problem, not proof the
worker stopped. Without a trustworthy liveness signal, stale open operations show
unknown status rather than a fabricated failure or continuing animation forever.

Serve bundled assets and APIs from the same origin. Accept only validated run IDs
and owned state paths; never expose arbitrary filesystem reads. Restrict origin
and host handling to local use. Server startup and graph extraction must not invoke
LLMs, create migration worktrees, or execute workflow effects.

## Metrics

Existing OTLP token counters are not a local historical usage store. Add normalized,
attributed usage records through the existing LLM usage seam when supported, with
run and execution identity and deduplication. Persist model, token categories and
pricing provenance for cost estimates. Missing usage/pricing is shown explicitly.
Wall time is independent of summed node durations, especially with parallel work;
parent totals must not count child usage twice. Live and historical panels use
the same persisted records. Final implementation may deliver unknown usage first,
but must not advertise complete cost coverage without verified integrations.

## Validation and builder discretion

Verify replay/live equivalence, snapshot-to-stream races, reconnect deduplication,
partial writes, corrupt records, missing metrics, nested runs, repeated visits,
recovery and unknown liveness. Test CLI startup, packaged static assets, path
confinement, and that attaching/detaching a monitor cannot affect a running worker.
Run appropriate Python checks and frontend build/type checks; exercise the browser
with fixture events and real journals without live model credentials.

Builder chooses Python HTTP library, frontend build tooling, graph layout engine,
module boundaries and polling interval. Use a focused dependency set; compiled
assets must be included in the distribution. Implementation starts after review
and approval of this design, followed by an implementation plan.
