# Refinery AI

Refinery AI is an early-stage, safe-by-default modernization factory for Java applications. It discovers a repository, builds a local knowledge graph, creates a typed migration plan, routes deterministic work toward OpenRewrite, and verifies every approved step.

Version **0.1.0** is a runnable architectural foundation. Discovery, Graphify AST indexing, deterministic planning, safety gates, OpenRewrite dry runs, build verification, repair budgets, typed Pydantic AI agent boundaries, and run reports are implemented. Repository investigation now uses a read-only Pydantic AI Harness FileSystem/RepoContext pilot and safe assessment Shell profile behind the `RepositoryEnvironment` seam. Source-changing OpenRewrite and AI execution remain explicitly review-gated.

## Architecture

```text
                Modernization Factory
                         │
                   Discovery Loop
                         │
                  Knowledge Graph
                         │
                    Plan Loop
                         │
              ┌──────────┴──────────┐
              │                     │
       OpenRewrite            AI Migration
       deterministic             reasoning
              │                     │
              └──────────┬──────────┘
                         │
                  Verification Loop
                         │
                ┌────────┴────────┐
              GREEN             FAILURE
                │                   │
             next step         Repair Loop
                │                   │
                └───────────────────┘
```

The factory—not an unconstrained model—owns control flow, repository mutation, validation, and limits. Pydantic AI agents return validated Pydantic models at bounded reasoning points.

## What v0.1 does

- Detects Maven and Gradle repositories.
- Inspects modules, Java/test file counts, Maven dependencies, Java version, frameworks, Git branch, and worktree state.
- Builds a provider-neutral context graph from repository evidence.
- Uses Graphify as a context provider for code symbols and cross-file relationships.
- Adds Spring semantic context for beans, dependency injection, HTTP endpoints, configuration,
  persistence mappings, Kafka listeners, and cache usage.
- Adds infrastructure context from database drivers and SQL migrations, Kafka clients and
  configuration, and Redis templates, keyspaces, and connection configuration.
- Stores the normalized graph under `.refinery/context/context-graph.json` while retaining
  provider-native artifacts for diagnostics.
- Produces and validates an ordered `ModernizationPlan`.
- Provides typed Pydantic AI planning, migration, and repair agents.
- Runs allowlisted OpenRewrite recipes in dry-run mode.
- Selects Maven or Gradle verification commands without invoking a shell.
- Refuses apply-mode operation on a dirty worktree by default.
- Provides a read-only Harness FileSystem/RepoContext assessment factory.
- Runs explicitly approved argument vectors through a time/output-bounded Harness Shell pilot.
- Spills oversized tool output to durable external artifacts before context compaction.
- Delegates build/runtime investigation to one bounded read-only Harness specialist.
- Stops repeated failures and enforces repair-attempt budgets.
- Persists JSON and Markdown run records in `.refinery/runs/`.
- Stops at a review gate before applying OpenRewrite or AI-generated source changes.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (`uv sync` installs the `graphifyy` package)
- A JDK and Maven/Gradle, or the project wrappers, when running verification
- An AI provider API key only when using the Pydantic AI agents

## Setup

```bash
git clone git@github.com:YOUR-ORG/refinery-ai.git
cd refinery-ai
uv sync
uv run refinery version
```

Run all local quality checks:

```bash
make check
```

Or run them individually:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Usage

Inspect a Java repository without changing it:

```bash
uv run refinery discover /path/to/java-project
```

Build and query its repository context graph:

```bash
uv run refinery graph \
  "Which services depend on the persistence layer?" \
  --repository /path/to/java-project
```

Planning and run commands automatically rebuild the context graph. The initial Graphify provider
produces its native files and the builder produces the normalized artifact:

```text
graphify-out/
├── graph.json
├── manifest.json
└── .graphify_analysis.json
.refinery/context/
└── context-graph.json
```

Code-only extraction does not generate the optional HTML visualization or narrative report. Those can be produced separately with Graphify's clustering and export commands.

Create and persist a migration plan:

```bash
uv run refinery plan \
  "Upgrade this application to Java 17" \
  --repository /path/to/java-project \
  --target-java 17
```

Run approved commands. In v0.1 this establishes a build baseline, performs an OpenRewrite dry run, and then stops for review:

```bash
uv run refinery run \
  "Upgrade this application to Java 17" \
  --repository /path/to/java-project \
  --target-java 17 \
  --apply
```

Without `--apply`, `run` is non-mutating and behaves like `plan`.

## Configuration

Settings use the `REFINERY_` prefix and may be placed in `.env`. See `.env.example`.

| Setting | Default | Purpose |
|---|---:|---|
| `REFINERY_MODEL` | `openai:gpt-5-mini` | Pydantic AI model identifier |
| `REFINERY_MAX_REPAIR_ATTEMPTS` | `3` | Repair-loop attempt limit |
| `REFINERY_COMMAND_TIMEOUT_SECONDS` | `900` | Build command timeout |
| `REFINERY_GRAPHIFY_TIMEOUT_SECONDS` | `900` | Graphify extraction timeout |
| `REFINERY_ALLOW_DIRTY_WORKTREE` | `false` | Permit apply mode with existing changes |

## Package layout

```text
src/refinery_ai/
├── agents.py        # typed Pydantic AI agents
├── cli.py           # CLI boundary
├── config.py        # environment configuration
├── context.py       # typed durable context receipts and artifacts
├── context_graph.py # provider-neutral collection, merge, storage, and queries
├── discovery.py     # deterministic Maven/Gradle inspection
├── factory.py       # workflow and safety gates
├── harness.py       # read-only Harness pilot and safe Shell policy
├── infrastructure.py # database, Kafka, and Redis context providers
├── knowledge.py     # Graphify context provider and compatibility adapter
├── models.py        # shared Pydantic contracts
├── openrewrite.py   # allowlisted dry-run adapter
├── planning.py      # deterministic planner and validation
├── repair.py        # repair-loop budgets
├── reporting.py     # JSON/Markdown audit output
├── repository.py    # Refinery-owned repository environment seam
├── specialists.py   # typed specialist output and deterministic merge
├── spring.py        # Spring annotation and framework semantic provider
└── verification.py  # bounded build execution
```

## Safety model

Refinery AI separates facts, decisions, and effects:

1. Deterministic discovery records observed facts.
2. Context providers collect facts with source provenance; Graphify initially supplies AST facts.
3. The context builder deterministically merges provider fragments into a versioned graph.
4. Planning produces typed proposals; the agent boundary accepts scoped repository context.
5. Policy code validates plans before execution.
6. Only the factory may run commands or apply changes.
7. Every step defines acceptance checks.
8. OpenRewrite recipes are allowlisted and initially run in dry-run mode.
9. Generated patches are proposals; v0.1 never applies them automatically.
10. Failures stop the run, and repair attempts have hard budgets.

Subprocesses receive argument lists directly and are never invoked through a shell. Stored command output is bounded.

## Current limitations

- Gradle discovery does not yet parse dependencies or Java toolchains.
- Graphify is run in local, code-only mode. Semantic providers currently perform deterministic
  source and configuration analysis; deeper Java type resolution, runtime topology, dynamically
  constructed SQL, topics, and Redis keys are not yet inferred.
- OpenRewrite dry runs require the plugin/recipe dependencies to be resolvable by the target build.
- OpenRewrite source application is review-gated.
- AI agents are available as typed boundaries but are not invoked by the default CLI workflow.
- Harness is the sole built-in assessment environment and is used for repository investigation, context controls, and one build/runtime specialist. Graphify and OpenRewrite remain Refinery-owned domain tools behind that environment; the architect/planner, persistence, and other specialists remain unchanged.
- Harness Shell command checks are policy guardrails, not OS-level network isolation. Assessment configuration denies network authority, and no network-capable command is exposed, but hard egress isolation requires a future sandbox/container execution environment.
- Proposed AI patches are not automatically applied.
- No web service, UI, pull-request automation, or remote-repository cloning.

## Roadmap

The next milestone will feed scoped Graphify queries into the executable planning and repair agents, then add OpenRewrite patch approval/application and fixture-based real-build integration tests.

## License

Apache-2.0
