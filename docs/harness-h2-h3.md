# Harness migration H2/H3

## H2 — read-only pilot

`create_read_only_assessment_harness` constructs a Pydantic AI agent with only:

- Harness `FileSystem` rooted at the selected repository with `read_only=True`;
- Harness `RepoContext` rooted and bounded to the same repository;
- explicit denial of `.git`, environment files, key files, and secret paths;
- a Refinery instruction boundary treating repository content as untrusted evidence.

It does not include model-facing Shell or writable FileSystem. Planning and one bounded SubAgent were added later in H6. H4 migrated the default investigator; the later feature-flag task still owns user-selectable rollback.

`HarnessRepositoryEnvironment.read_file` and `find_files` pilot line-addressable evidence reads and repository discovery. Current `DiscoverySnapshot`, `KnowledgeGraphInfo`, and `VerificationResult` models remain the adapter outputs. Deterministic discovery, Graphify, and OpenRewrite remain delegated to the legacy environment until their scheduled migration phases.

## H3 — safe Shell pilot

`AssessmentHarnessPolicy` accepts argument vectors rather than interpolated shell text and authorizes only bounded inspection, tool-version, and test commands. It rejects:

- non-allowlisted executables;
- mutating Git subcommands;
- `find -delete`, `-exec`, output-writing actions, and equivalents;
- in-place `sed`;
- build-tool goals other than version reporting and `test`;
- writable or network-authorized assessment configurations.

Authorized vectors are quoted with `shlex.join` and executed by Harness `Shell` with a fixed repository root, timeout, output cap, no persistent `cd`, no interactive commands, blocked shell operators, a minimal environment, and provider-key filtering. Results are translated back to the existing Refinery `VerificationResult`, including exit code 124 for timeout and bounded stdout/stderr.

Harness itself documents local Shell command filters as guardrails rather than OS isolation. H3 therefore does not claim hard egress isolation. The assessment policy grants no network authority and exposes no obvious network client, while true network denial remains a prerequisite for any later untrusted-agent execution environment.

## Acceptance evidence

Run:

```text
uv run pytest tests/test_harness.py
make check
```

Tests cover read-only factory configuration, evidence line locations, file discovery, path traversal, secret denial, protocol compatibility, allowed command execution, command failures, mutation denial, build-goal denial, authority-policy rejection, oversized durable evidence, typed specialist output, bounded delegation configuration, deterministic deduplication, and conflict preservation.

## H4 — repository investigator migration

The current MVP's deterministic `discover` operation is its repository investigator; there is no separate investigator agent or typed `Finding`/`KnowledgeGap` pipeline yet. H4 moves this operation to `HarnessRepositoryEnvironment`:

- build-file and Java-source inventory uses Harness FileSystem;
- Maven metadata is parsed from line-addressable Harness evidence;
- branch and dirty-state observations use the H3 safe Shell path;
- results remain the unchanged `DiscoverySnapshot` domain model;
- `rule_based_plan`, Pydantic planning agents, Graphify, persistence, and OpenRewrite are unchanged.

The CLI `discover` command and the factory's default discovery path now use the Harness investigator. Explicitly injected `RepositoryEnvironment` implementations still work, preserving test and rollback seams. The H4 parity test compares all normalized snapshot fields against `LegacyRepositoryEnvironment`, asserts no fixture writes, and applies a deliberately generous local latency ceiling to detect material regressions without treating machine noise as a product failure.

Because the current MVP has no typed findings or gaps, H4 cannot compare artifacts that do not exist. The equivalent acceptance signal is complete `DiscoverySnapshot` parity and unchanged planner inputs. Adding new finding/gap schemas remains outside this migration phase.

## H5 — context controls

The read-only assessment agent now composes:

- Harness `ToolOutputLimits` with lossless spill and bounded truncation fallback;
- an external `LocalFileStore` keyed by repository identity, never inside the assessed repository;
- Harness `ClearToolResults`, retaining the three newest tool-call/result pairs;
- `WarnNearLimits` at 90% of the model context window.

Refinery additionally owns `ContextReceipt`, `ContextArtifact`, and `DurableContextStore`. Oversized direct evidence reads produce a bounded head/tail preview and a typed receipt containing the content hash, byte count, creation time, kind, and stable handle. Reading through the receipt verifies both stored and expected hashes. Durable truth therefore remains addressable after tool output leaves the active context, without introducing a vector store or changing domain/planner models.

H5 writes context artifacts only to the configured external context store. The assessed repository remains unchanged.

## H6 — first specialist delegation

The assessment harness now includes Harness `Planning` and exactly one Harness `SubAgents` delegate: `build-runtime-specialist`. The specialist has its own read-only FileSystem and RepoContext, receives no inherited parent tools, cannot delegate recursively, is limited to one call per parent run, has a wall-clock timeout, shares usage accounting, and contains failures for parent-level recovery.

The specialist returns the framework-neutral `BuildRuntimeSpecialistResult`. `merge_specialist_results`:

- deduplicates identical key/value observations;
- unions and sorts repository-relative evidence paths;
- sorts questions and outputs for order-independent results;
- retains contradictory values as explicit `SpecialistConflict` records rather than selecting a winner.

Only build/runtime has migrated. Architecture/API, persistence, testing, and operations/security specialists remain deferred. The modernization planner and its domain contracts are unchanged.
