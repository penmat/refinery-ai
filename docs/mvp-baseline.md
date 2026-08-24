# Current MVP baseline (H0)

Captured on 2026-08-23 before replacing any production repository behavior.

## Repository tool inventory and call sites

| Current tool | Effects and behavior | Production call sites | Future capability mapping |
|---|---|---|---|
| `discovery.discover` and `_git` | Reads build/source metadata; invokes `git status` and `git branch`; returns `DiscoverySnapshot`; missing repository raises `ValueError` | CLI `discover`; `ModernizationFactory.prepare` | Harness FileSystem + RepoContext; Git remains a Refinery-owned domain operation |
| `GraphifyKnowledgeGraph.build/query` | Runs Graphify with a timeout/output cap; build writes `graphify-out`; raises typed `KnowledgeGraphError` on missing executable, timeout, non-zero exit, or missing graph | CLI `graph`; `ModernizationFactory.prepare` | Safe Harness Shell plus Refinery-owned knowledge/domain tool |
| `verification_command` / `verify` | Selects Maven/Gradle command; executes an argument vector without a shell; records duration; bounds and ANSI-cleans output; maps missing command to 127 and timeout to 124 | `ModernizationFactory.run`; OpenRewrite adapter | Safe Harness Shell; command selection remains Refinery-owned policy |
| `OpenRewriteAdapter.dry_run` | Enforces recipe allowlist and runs Maven/Gradle dry-run command; may create tool/build outputs in the target repository | `ModernizationFactory.run` | Retained Refinery domain tool over safe Harness Shell in a later phase |
| `ModernizationFactory._save` | Creates `.refinery/runs` and writes JSON/Markdown reports | `prepare` and `run` | Persistence remains Refinery-owned and is not migrated in H0/H1 |

`RepositoryEnvironment` now owns the orchestration-facing calls. The legacy adapter delegates to these same implementations. Domain models, planner code, prompts, persisted schemas, and command spelling are unchanged.

## Repeatable baseline

The representative Maven fixture is `tests/fixtures/legacy-java`. Golden normalized artifacts are in `tests/fixtures/golden`; machine-specific repository paths, Git state, timestamps, run IDs, and measured durations are intentionally excluded.

Run the baseline with:

```text
uv run pytest tests/test_baseline.py tests/test_repository.py
uv run refinery discover tests/fixtures/legacy-java
uv run pytest
make check
```

The smoke tests capture:

- successful discovery and deterministic planning artifacts;
- a zero-write footprint for discovery;
- the exact executed argument vector in each `VerificationResult`;
- non-negative wall-clock duration;
- non-zero command failure propagation;
- large-output tail truncation at the configured cap.

## Known baseline boundaries

- The current MVP has no `KnowledgeGap` artifact or question workflow, so H0 records the existing AI review step as the current representation of unresolved gaps; adding new gap schemas would violate the migration constraint.
- Graphify build and factory planning are not read-only: they write `graphify-out` and `.refinery/runs` respectively. Only discovery is claimed and tested as zero-write.
- OpenRewrite and real Maven/Gradle timing are environment- and dependency-cache-sensitive. Unit smoke tests establish the timing/failure contract without requiring network access.
- The fixture does not freeze prose from a model because the default CLI planner is deterministic and the Pydantic AI boundaries are not invoked by the default workflow.
