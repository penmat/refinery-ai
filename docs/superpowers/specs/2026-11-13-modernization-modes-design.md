# Refinery modernization modes

Status: approved destination-first design. Implementation is on the current branch by explicit user authorization; see the implementation plan for coverage and limitations.

## Intent and agreed decisions

Let a user supply only a target JDK and have Refinery choose a coherent modernization destination. Offer minimal, recommended (default), and full modes. Recommended may select sensible major framework upgrades. Resolve and save the destination before executing migration cycles. The plan must support later review/editing, but an interactive review loop is outside this release.

The user selected a separate destination-planning stage over allowing every migration cycle to choose its own destination. The latter is cheaper initially, but risks target drift and makes future review difficult. Static profiles remain supported for existing callers; multiplying fixed profiles for every JDK/mode combination would not satisfy dynamic planning.

## Proposed scope policy

| Concern | Minimal | Recommended | Full |
|---|---|---|---|
| JDK | Requested target | Requested target | Requested target |
| Frameworks | Retain unless compatibility requires change | Choose coherent baseline; major upgrades allowed when justified | Same compatibility discipline; broader modernization opportunities |
| Dependencies | Changes needed for JDK/framework compatibility | Also prioritize security fixes and clearly beneficial maintenance upgrades | Broad dependency assessment, upgrades, and justified replacements/removals |
| Source modernization | Compatibility changes only | Selective, low-risk improvements with concrete benefit | Broader recipe-supported improvements grounded in findings |
| Dependency/OSV analysis | Off; inspect build declarations | On | On |
| SonarQube | Off | Off | On |
| CodeGraph | Off | Off | On |

This matrix beyond the user's stated major-upgrade and Full-analysis preferences is the designer's proposal. Full is not permission to rewrite architecture, replace frameworks wholesale, adopt preview features, or apply every recipe. Dependency replacement requires usage evidence and a supported transformation. Preserve build/BOM ownership rather than independently upgrading managed transitive dependencies.

## CLI and compatibility

Proposed commands:

```bash
refinery plan --target-java 21
refinery plan /path/to/project --target-java 21 --mode full
refinery run --repository /path/to/project --target-java 21 --apply
refinery run --repository /path/to/project --modernization-plan PLAN_ID --apply
```

Make the existing plan subject optional, defaulting to the current directory. Retain existing positional repository and free-text goal behavior. With an explicit target and no named profile, select mode planning; default mode to recommended. Mode without a target is an actionable error. Explicit --mode and --profile conflict. Existing named-profile and goal-only paths retain their semantics. A saved-plan run derives its target/mode from that artifact and rejects conflicting flags. Explicit --apply is still required for source changes. Planning may write assessment/state artifacts and use configured analysis services, but never changes application sources.

No additional version overrides, review UI, or manual JSON-edit contract are required in this release. The structured format leaves room for them. Corpus mode support is deferred; its named-profile behavior stays intact.

## Two layers of planning

1. Discover project technologies, versions, modules, dependency ownership, and build constraints. Collect evidence according to mode.
2. Resolve a versioned ModernizationIntentPlan (new model; do not overload the existing ModernizationPlan). The model chooses targets and a finite ordered set of objectives, using repository evidence and inspected recipe metadata. Save it before any migration.
3. The existing recipe planner produces bounded executable batches toward those objectives. It may adapt ordering and compatibility prerequisites within the saved scope, but may not change target versions or add unrelated modernization goals.
4. Apply batches using existing isolated migration, build/test verification, repair budgets, checkpoints, and publication machinery. Persist progress separately from the destination plan.

Use the existing configured planner provider and LangGraph integration. No new provider onboarding or execution backend is needed. Keep destination resolution separate from recipe batching so the existing batch-size limit does not truncate the overall plan.

## Target selection and grounding

The requested JDK is a constraint, not a suggestion; never raise it silently or downgrade a project already above it. Reject a lower requested JDK as unsupported in this release. Do not infer that a Spring project exists when discovery says otherwise. Scope initial measurable framework targets to the existing Java/Spring capabilities; other detected frameworks may use validated catalog transformations but must be reported as unsupported where compatibility cannot be established.

Use discovered build constraints and inspected recipe metadata/composition as evidence for available destinations and transformations. Add a small, testable compatibility knowledge provider for supported framework/JDK combinations where recipe metadata lacks that fact. Entries carry source references and verification dates; implementers must verify their facts against primary documentation. Release/version metadata is cached with provenance; pin concrete versions in saved plans, never execute dynamic 'latest' selectors. Do not claim current support status or 'latest' from model recollection or recipe names alone. Expanding to arbitrary framework version resolution is a non-goal.

Recommended balances compatibility, upgrade effort, ecosystem fit, maintenance/security benefits, and available transformations. Prefer a verified coherent destination over the newest candidate. Full broadens scope rather than weakening compatibility checks. Unknown compatibility becomes a documented limitation; if it blocks a required target, the plan is blocked and cannot execute. Best judgment does not mean invented compatibility facts.

### Initial version sources and dependency execution (designer decisions)

Use a bundled, sourced, dated compatibility table for supported JDK/Spring destinations in the first release, including concrete framework patch targets and canonical recipes. It is updated with Refinery releases; no live vendor support-status crawler is included. For dependency candidate versions, use Maven repository metadata from configured accessible repositories with bounded requests and caching. Do not expose repository credentials to the model. On fetch failure, retain cached provenance or mark candidates unresolved; never guess versions. Metadata proves a version exists, not that it is compatible. Scope initial automatic dependency changes to direct dependencies and explicitly owned build properties/BOMs; BOM-managed transitive updates happen through their owner.

Add bounded parameterized recipe execution for dependency upgrades: recipe steps carry validated options for inspected Maven/Gradle dependency-upgrade recipes, including exact group/artifact and target version. The executor generates the necessary wrapper recipe through its existing OpenRewrite integration. Persist the options and include them in execution identity, receipts, deduplication, and resume checks. Reject unknown option keys, unsupported recipes, dynamic versions, and coordinates outside the saved objective. This is not a general arbitrary-recipe authoring feature. Changes requiring custom transformations with no supported executor remain advisory; a mandatory compatibility change without an executor blocks the plan.

Version existence and recipe availability do not establish dependency compatibility. Use available declared constraints and project evidence, label residual uncertainty in the plan, and rely on isolated build/test verification before publishing. Explicitly incompatible versions are excluded. Required framework/JDK compatibility must be established by the compatibility provider.

## Saved plan contract

Persist schema version, plan ID, creation time, repository identity, source revision and relevant build/source fingerprints, requested JDK, mode, policy version, chosen targets, evidence provenance, analysis availability, assumptions, exclusions, limitations, and ordered objectives.

Each objective has a stable ID, category, rationale, evidence references, dependencies, target/component coordinates when applicable, candidate validated transformations, and a completion predicate. Distinguish executable objectives from advisory recommendations without an available executor. Advisory items are explicitly deferred and never treated as completed work. A missing required transformation blocks execution.

Write canonical JSON plus a readable Markdown summary under .refinery/modernization-plans/<plan-id>.*. Link every child PersistedMigrationPlan and run to the parent plan ID and content digest using additive optional fields, preserving old artifact compatibility. Store progress and migration receipts separately. Resume pins the saved plan/digest and original execution settings; it must never silently resolve a different destination. Unexpected external edits to relevant inputs invalidate execution with a regenerate-plan message, while published changes owned by the same run advance its expected fingerprint.

The future review layer can create a new validated plan revision. This release does not implement editing, revision merging, or approval prompts.

## Policy enforcement and completion

Prompts describe policy; deterministic validation enforces structured targets, inspected executable recipes, dependency ordering, JDK ceilings, and scope categories. Inspect composite recipe effects: minimal cannot accidentally invoke optional language transformations through a broad Java upgrade recipe. Unknown composite effects must be excluded or reported as a limitation rather than assumed safe. Reuse target finalizers where known recipes temporarily reset Java versions.

For platform/dependency objectives, verify discovered versions (including exact selected framework/dependency patches) against the saved targets. For source objectives, require successful verified transformation receipts plus a defined follow-up check where available. A verified no-change application can satisfy a source objective if the transformation explicitly establishes it is already satisfied; failure or mere omission from the next batch cannot. Findings-based objectives require a comparable follow-up analyzer result; scanner unavailability leaves them unverified.

Completion means all executable objectives are satisfied and the final build/tests pass. Display analysis gaps and deferred advisory items alongside that status; never claim all findings were fixed. Stop on failed migration, repeated unsatisfied plan, no measurable progress after recheck, unmet targets with an empty batch, or cycle/repair limits. Broader evidence on subsequent cycles may inform how to execute the saved objectives but does not enlarge their scope.

## Analysis failures and freshness

Full attempts dependency analysis, SonarQube, and CodeGraph with existing configuration and timeouts. If an optional analyzer is unavailable during initial resolution, produce a degraded plan using remaining evidence and defer optional opportunities that cannot be grounded. Do not install services automatically. A required platform compatibility gap blocks execution of the whole plan before source changes. Once a plan is executable, losing objective-specific evidence blocks that objective and its dependents, while independent objectives may continue. If any selected executable objective remains blocked or unverified, the overall run ends incomplete with a nonzero CLI exit status, even if independent verified changes were published. Deferred advisory opportunities from initial resolution do not prevent executable-objective completion, but must remain visible in its degraded-coverage report. Never silently downgrade a selected executable objective to advisory merely to claim success.

Refresh dependency evidence when build inputs change; refresh CodeGraph when indexed sources change. Refresh SonarQube after relevant source/build changes when used to verify findings, with a final scan for findings-based objectives. Do not reuse the current dependency-only Sonar invalidation rule for source-quality verification. Planning scans may be expensive; cache valid results and display availability clearly.

## Integration and validation

Expected seams: new mode policy and intent-plan models/store/resolver; CLI dispatch in cli.py; evidence gathering and constrained batching in factory.py and agents/planning.py; parent linkage in models.py; objective tracking and checkpoint identity in profile_run.py/workflows/profile.py; and reporting. Reuse profile machinery where it fits without pretending arbitrary source objectives are Java/Spring version gaps. Keep legacy named-profile completion behavior unchanged.

Tests must cover default CLI selection and legacy compatibility; mode boundaries including composite recipes; major-upgrade selection from fixtures; unsupported/non-Spring targets; exact target pinning; analyzer failure/degradation and cache invalidation; save/load and old artifact compatibility; resume without target drift; external mutation rejection; finite objective completion; and failure/empty/repeated-plan termination. Use fake planner/analysis responses for deterministic tests. Live model/network tests are optional and separate from required checks.

Builder discretion: internal module names, detailed schema field names, caching implementation, and extraction/refactoring of shared orchestration. Preserve behavior and public CLI compatibility specified above.

## Workspace status

The user authorized implementation on the current branch after managed worktree
creation failed. The design and plan are now stored in this repository. Existing
unrelated untracked files and running application processes are preserved.
