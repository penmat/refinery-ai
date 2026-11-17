# Monitor node details implementation plan

**Goal:** Show the latest discovery summary on Analyze and one recipe on Migrate.

**Design:** [Monitor node details](../specs/2026-11-06-monitor-node-details-design.md)

**Execution:** Implement backend then UI in this chat. Keep the current journal
schema compatible and preserve pending workspace changes.

### 1. Record discovery and recipe details

- [ ] Add failing tests in `tests/test_monitor_recording.py` and
  `tests/test_monitor_journals.py`: discovery facts survive journal replay;
  recipe details belong to the latest step; a previous recipe's failure does
  not carry over; old records without summaries still open.
- [ ] Define optional `AnalysisSummary` on `MonitorEvent` with
  `java_version`, `spring_boot_version`, `build_system`, and `frameworks`.
  Have the CLI Analyze callback return that summary alongside its existing
  Sonar availability flag, with existing bool callbacks still accepted.
  Carry the latest analysis result through the Analyze wrapper into its
  completed event, including when the first attempt falls back to a fresh scan.
- [ ] Record concise `failure_reason` on migration events from the post-operation
  state; do not expose tool output or exception text. Extend the run summary
  with `recipe`, `step_status`, and `failure_reason` from the latest recipe
  step, preserving its existing name and position. Scope step history to
  the latest recipe and recovery generation; usage events do not change status.
- [ ] Run focused journal, recording, profile, and migration regression tests.
  Check that analysis completion still uses the correct Sonar flag.

### 2. Render the focused details

- [ ] Add failing UI tests in `web/monitor/src/App.test.tsx`: Analyze shows
  discovered Java and build facts; Migrate shows just the matching child
  recipe with its ID and failure reason, even with an older child and an
  ordered plan list in history; missing data has an explicit empty state.
- [ ] Add the optional fields to `web/monitor/src/state.ts`. In the existing
  details panel, show the latest Analyze visit or a concise missing-data
  message. For Migrate, choose the latest child correlated to the latest
  Migrate visit and show only its current recipe and a link to that child.
  Retain the generic details and ordered list for Plan and other nodes.
- [ ] Run frontend tests, rebuild the bundled assets, and check Python types,
  style, and the diff. Verify the visible result with representative local
  journals. Report limitations for older journals.

**Review focus:** Retries, earlier child runs, failed steps, absent discovery
fields, and older journals must not produce misleading details.
