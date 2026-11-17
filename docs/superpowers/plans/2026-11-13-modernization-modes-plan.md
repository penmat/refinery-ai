# Modernization Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Resolve and persist a grounded Minimal / Recommended / Full destination from a requested JDK, then execute bounded migration batches without target drift.

**Architecture:** Add a `modernization` package for intent, policy, evidence, compatibility, resolution, persistence, and objective progress. A dedicated LangGraph mode runner reuses the existing migration subgraph and verified publication machinery. Existing named profiles keep their current orchestration and completion behavior.

**Tech Stack:** Python 3.12+, Pydantic 2, Typer, LangGraph, existing OpenRewrite/Maven/Gradle and analysis integrations; pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-13-modernization-modes-design.md`. User approved the destination-first design in the handoff.

## Workspace prerequisite

The user explicitly authorized implementation on the current branch after worktree
creation failed. The implementation uses the existing virtualenv without changing
its installed dependencies. All changes remain uncommitted; unrelated untracked
files are preserved.

## Global constraints

- Target JDK is the only required modernization parameter. Default mode is `recommended`.
- `minimal` changes compatibility only; `recommended` permits justified major framework upgrades, useful dependencies, and selective source improvements; `full` adds broad dependency, SonarQube, and CodeGraph evidence.
- Minimal dependency/OSV, SonarQube and CodeGraph collection is off. Recommended dependency/OSV is on and SonarQube/CodeGraph are off. Full attempts all three with existing configured services and bounded timeouts.
- Preserve named profiles, legacy goal-only paths, and corpus behavior. Explicit mode plus profile is invalid. Mode requires target JDK. Saved-plan execution rejects conflicting flags.
- Resolve and save immutable targets before migrating; explicit `--apply` is required to change sources. No review/edit UI, arbitrary framework replacement, preview features, or live support-status crawler.
- Never silently downgrade Java or change selected targets. Concrete versions and executable transformations require provenance; unknown required compatibility blocks execution.
- Save JSON and Markdown in `.refinery/modernization-plans/<plan-id>.*`; progress is separate. Honor configured state-directory overrides consistently.
- Completion requires every executable objective and final build/tests verified; deferred advisory findings remain visible. Missing executable-objective evidence means incomplete/nonzero exit.
- Security remediation is explicitly included in Recommended and Full. Resolve direct and transitive dependencies, create actionable remediation objectives for known vulnerabilities where a compatible supported fix exists, and report every unresolved finding. No severity is silently excluded; severity determines priority. Minimal remains compatibility-only.
- Existing credentials/provider configuration is reused. No live model calls are required by tests.

## Review focus

1. Mixed-version multi-module builds must not appear complete from only the root module; Task 2 pins per-module observations.
2. Adding optional model fields can change old migration digests and invalidate receipts; Task 6 preserves legacy digest serialization.
3. Compact catalog listing currently marks all entries allowed, without inspecting composition; Task 3 separates selection from inspection.
4. Identical recipe names with different coordinates/options must not be deduplicated as identical work; Tasks 5 and 6 pin full execution identity.
5. A crash between publication and progress update must recover owned changes without accepting unrelated edits; Task 8 pins receipt-based reconciliation.

## Existing integration facts

`RecipePlan` has a 13-recommendation cap. Destination objectives must use a different model without that batch cap. `ModernizationProfile.unresolved_targets()` compares Java and Spring major/minor, not exact patches or source findings. `workflows/profile.py` treats an empty batch with no platform gaps as completion and caches Sonar using dependency inputs only. Keep these legacy semantics out of mode completion.

`RecipeCatalogSession.compact_index()` calls `_allow()` for every source. Add independent inspection tracking; do not change legacy behavior incidentally. `factory.prepare()` adds deterministic prerequisites after planner output; mode validation must run after additions and finalizers. `RunManifest.plan_digest` hashes complete model JSON: additive fields need explicit compatibility handling. `load_migration_failures()` currently records completion by recipe name across artifacts; mode histories need parent, revision, objective and options scoping.

## Version selection and security policy

Construct the destination from resolved dependency evidence, published Maven versions, sourced compatibility constraints, framework BOMs, OSV advisories and inspected transformations. The model ranks grounded candidates; it must not invent versions or infer compatibility merely from version existence or semantic version numbering.

For Recommended and Full, prefer a compatible patched release in the existing release line when it resolves the known advisories; allow a minor or major upgrade when necessary for remediation or justified by framework compatibility and maintenance benefits. Select a coherent framework destination first and use its managed dependency set. For a vulnerable transitive dependency, identify its owning direct dependency, property or BOM and upgrade that owner; do not independently override managed transitives without a supported, explicitly validated transformation. Validate the resulting resolved graph, not only the owner's version.

Record candidate versions considered, rejection reasons, chosen exact version, compatibility evidence, advisory IDs/aliases, source URLs and retrieval dates. Missing fixed-version metadata does not mean no fix exists: query published candidate versions against OSV and consult available advisory references. Missing compatibility evidence or an unsupported executor becomes an explicit unresolved finding, not an invented fix. Known actionable fixes must not be omitted at the model's discretion.

A security objective is complete only when the selected exact target is reached, the targeted advisory no longer affects the relevant resolved dependency paths/modules in a fresh successful scan, and final build/tests pass. A scanner outage is unknown coverage, never a clean result. Initial unavailable evidence may yield a degraded plan with visible limitations; loss of evidence needed to verify an executable security objective leaves the run incomplete. Newly discovered vulnerabilities are reported for replanning without silently changing the saved destination. Do not claim the application is vulnerability-free or exploitability is proven by a package-version match.

## Task 1: Intent schema, policy and immutable storage

**Files:** Create `src/refinery_ai/modernization/{__init__,models,policy,store,progress}.py`; create `tests/test_modernization_models.py`, `tests/test_modernization_store.py`.

**Interfaces:** `ModernizationMode` enum; `ModePolicy` and `mode_policy(mode: ModernizationMode) -> ModePolicy`; `ModernizationIntentPlan`; `ModernizationObjective`; `TargetSelection`; `EvidenceReference`; `AnalysisAvailability`; `CompletionPredicate`; `TransformationSpec`; `IntentPlanStore(repository: Path, state_directory: Path)`, `.save(plan) -> Path`, `.load(plan_id: str) -> ModernizationIntentPlan`; `intent_digest(plan) -> str`.

`ModernizationProgress` and `ObjectiveProgress` are declared in `modernization/progress.py` in this task: parent ID/digest, expected revision/fingerprints, pinned execution settings, cycle count, child IDs, and per-objective pending/satisfied/blocked states with verification evidence. Task 8 adds their store and evaluation logic.

Models use strict extra-field rejection. Intent records schema/policy versions, ID/time, repository identity, source revision and build/source fingerprints, requested JDK, mode, concrete targets, provenance, assumptions, exclusions, limitations, analyzer coverage, executable/blocked status and ordered objectives. Objectives have stable IDs, category, rationale, evidence IDs, dependencies, component/module coordinates, required flag, executable/advisory disposition, transformations and typed completion predicate (version, transformation receipt, or finding recheck). Security objectives additionally retain affected coordinates/modules, advisory IDs and aliases, the dependency owner to change, selected target, remediation rationale and a fresh vulnerability-recheck predicate. Transformation options are bounded scalar mappings, never command strings. Validate unique IDs, references and acyclic order; mandatory unexecutable objectives block the plan. Validate unknown schema versions and safe IDs.

- [ ] Write `test_mode_policy_matrix`, asserting the exact matrix above, and `test_intent_rejects_invalid_graph`, asserting duplicate IDs, missing evidence/dependencies, cycles and dynamic target versions fail.
- [ ] Write `test_store_roundtrip_and_immutability`: JSON roundtrip preserves intent digest, Markdown includes rationale/limitations, identical resave is idempotent, changed content under the same ID fails, and traversal IDs fail.
- [ ] Run `uv run pytest tests/test_modernization_models.py tests/test_modernization_store.py --no-cov -q`; expect failures for missing modules.
- [ ] Implement these interfaces with canonical sorted JSON and atomic writes. Treat JSON as authoritative; summary failure must be recoverable without mutating intent.
- [ ] Rerun the command; expect all tests passing. Commit this task's files.

## Task 2: Project evidence and dated compatibility knowledge

**Files:** Create `modernization/{evidence,compatibility,versions}.py`, `modernization/data/compatibility.json`, `tests/test_modernization_evidence.py`, `tests/test_modernization_compatibility.py`, `tests/test_modernization_versions.py`; extend `discovery.py` only where module/ownership facts are missing. Modify `inspectors/dependencies/{models,osv,inspector,collectors}.py`; extend `tests/test_dependencies.py` and create `tests/test_modernization_security.py`.

**Interfaces:** `ProjectEvidence` carries snapshot, per-module versions, dependency declarations/owners, provenance, fingerprints and analyzer outcomes. `collect_evidence(repository: Path, policy: ModePolicy, settings: Settings) -> ProjectEvidence`; `CompatibilityProvider.candidates(evidence: ProjectEvidence, target_java: int) -> list[TargetSelection]`; `VersionCandidateProvider.candidates(group: str, artifact: str) -> list[VersionCandidate]`. Candidate values include exact version, source, retrieval/verification date and uncertainty; compatibility candidates include constraints and canonical transformation identifiers.

- [ ] Write `test_collection_obeys_mode`: fake analyzers assert zero disabled calls, all three attempted for Full, and optional outages recorded as degraded availability without service installation.
- [ ] Write `test_modules_and_owners`: mixed Maven modules, inherited properties, imported BOMs and Gradle declarations preserve module identity and ownership; unresolved expressions remain unknown. Root compliance alone is not completion.
- [ ] Write `test_compatibility_constraints`: plain Java does not gain Spring targets; lower JDK request fails; unsupported required framework/JDK combination is blocked; candidate fixtures can include a justified major upgrade.
- [ ] Write `test_version_metadata_bounds`: fixed-version parsing, bounded timeout/response size, offline cache provenance, unknown metadata, credential redaction and no guessed candidate. BOM-managed transitive versions cannot be independently selected.
- [ ] Write `test_osv_preserves_remediation_evidence`: retain affected package/ecosystem, affected ranges and fixed events, explicit affected versions, advisory aliases/references, withdrawn status and retrieval/modification timestamps, with backward-compatible defaults. Cover multiple affected packages and release branches, missing fix events and Maven-specific version ordering; never assume generic SemVer ordering.
- [ ] Write `test_security_inventory_tracks_transitive_owners`: preserve resolved coordinates and dependency paths by module plus direct declaration/property/BOM ownership; unresolved ownership is explicit. Structured security evidence must survive assessment persistence and planner loading instead of being reduced to message text.
- [ ] Write `test_candidate_security_checks`: check published candidate versions against OSV; one advisory's fixed event does not establish freedom from other known advisories. Cover missing fixed metadata, withdrawal/aliases and incomplete service responses; never turn scan failure into an empty clean result.
- [ ] Run `uv run pytest tests/test_modernization_evidence.py tests/test_modernization_compatibility.py tests/test_modernization_versions.py tests/test_modernization_security.py tests/test_dependencies.py --no-cov -q`; expect new-interface failures.
- [ ] Extend the dependency Vulnerability contract with the structured fields above and preserve raw advisory provenance where needed. Add `SecurityFinding` and `SecurityRemediationCandidate` models to `modernization/models.py`; extend `ProjectEvidence` with structured inventory/findings/candidates. Implement `security_candidates(evidence: ProjectEvidence, versions: VersionCandidateProvider, compatibility: CompatibilityProvider) -> list[SecurityRemediationCandidate]` in `modernization/versions.py`, using an injected OSV client for candidate queries. Each candidate links exact proposed owner version, affected package, advisory IDs, compatibility evidence and unresolved reasons. Cache security results separately from version metadata and preserve scan time/coverage.
- [ ] Implement evidence adapters around existing dependency, assessment and context-graph integrations. Reuse configured repositories; keep authenticated transport out of planner payloads. Cache by coordinate/repository with provenance. Do not conflate version existence and compatibility.
- [ ] Verify concrete bundled framework patches and Java ranges against official Spring documentation and artifact metadata before adding entries. Store URLs and verification dates with each entry; test against fixtures, not live services. Include only destinations supported by inspected transformations; unavailable destinations block with a clear reason.
- [ ] Rerun tests and commit, including source references in the data. Do not label entries latest or currently supported unless the source establishes it.

## Task 3: Deterministic recipe effects and scope validation

**Files:** Create `modernization/validation.py`, `tests/test_modernization_validation.py`; modify `recipes/catalog.py`, `recipes/knowledge.py`; extend `tests/test_recipe_catalog.py`.

**Interfaces:** `RecipeCatalogSession.inspected_recipes` separate from `allowed_recipes`; `validate_intent(plan: ModernizationIntentPlan, evidence: ProjectEvidence, catalog: RecipeCatalogSession, compatibility: CompatibilityProvider) -> None`; `validate_batch(batch: RecipePlan, intent: ModernizationIntentPlan, evidence: ProjectEvidence, catalog: RecipeCatalogSession) -> None`.

- [ ] Write `test_index_does_not_prove_inspection`, `test_minimal_rejects_optional_composite_effect`, `test_unknown_composition_is_not_safe`, `test_scope_rejects_target_drift`, and `test_prerequisite_cannot_escape_scope`. Assert recursive effect inspection, cycle protection, requested JDK ceiling, exact target/coordinate matching and dependency order; unknown leaf effects require maintained evidence or exclusion.
- [ ] Run `uv run pytest tests/test_modernization_validation.py tests/test_recipe_catalog.py --no-cov -q`; expect new tests failing.
- [ ] Implement recursive composition/effect validation, preserving legacy catalog selection. Required compatibility work with no safe recipe blocks; optional unsupported work is advisory. Permit only prevalidated prerequisites in the saved objective closure.
- [ ] Rerun and commit.

## Task 4: Destination resolver with configured planner

**Files:** Create `modernization/resolver.py`, `agents/modernization.py`, `tests/test_modernization_resolver.py`; modify `agents/planning.py` only to share its model construction through a public helper if needed.

**Interfaces:** `IntentResolutionRequest` contains evidence, mode policy, compatibility candidates and recipe catalog. `DestinationPlanner.resolve(request: IntentResolutionRequest) -> ModernizationIntentPlan` protocol; `resolve_intent(repository: Path, target_java: int, mode: ModernizationMode, settings: Settings, *, planner: DestinationPlanner | None = None) -> ModernizationIntentPlan` collects, resolves, validates and saves before returning.

- [ ] Write `test_recommended_major_upgrade_is_grounded`, `test_minimal_retains_compatible_framework`, `test_full_degrades_without_inventing_findings`, `test_required_compatibility_blocks`, and `test_destination_not_limited_to_batch_size`. Use fake structured model outputs; assert exact JDK/patches, evidence references, more than 13 objectives, and save-before-any-execution ordering.
- [ ] Write `test_recommended_selects_security_remediation`, `test_security_prefers_compatible_patch`, `test_security_can_require_major_upgrade`, `test_security_updates_bom_owner`, `test_no_fix_or_unknown_compatibility_is_visible`, and `test_model_cannot_drop_actionable_security_finding`. Assert exact evidence-backed candidates, advisory-linked objectives and dispositions for every finding; Full is not required. An unsupported mandatory compatibility fix still blocks the whole plan; an initially unexecutable security finding is reported as unresolved advisory work, never silently claimed fixed.
- [ ] Implement deterministic coverage validation: every known finding has an executable remediation objective or an explicit unresolved disposition. Supply the version-selection policy and candidate/rejection evidence to the resolver and persist its rationale.
- [ ] Run `uv run pytest tests/test_modernization_resolver.py --no-cov -q`; expect interface failures.
- [ ] Implement separate structured-output planning with existing configured model and catalog tools. Reject unsupported model choices deterministically; use bounded validation retries, then return a blocked plan with reasons. Keep configured provider credentials out of persisted intent and rationale. No live provider setup is necessary.
- [ ] Rerun and commit.

## Task 5: Parameterized dependency transformations

**Files:** Modify `models.py`, `openrewrite.py`, `migrations/openrewrite.py`; create `modernization/transformations.py`, `tests/test_modernization_transformations.py`; extend `tests/test_openrewrite.py`.

**Interfaces:** Add optional `objective_id: str | None` and `options: dict[str, str | int | bool]` to `RecipeRecommendation`. `validate_transformation(step: RecipeRecommendation, objective: ModernizationObjective) -> None`; `execution_identity(step: RecipeRecommendation) -> str` hashes recipe, concrete artifact, canonical options and objective ID. Adapter accepts validated options and writes a scoped YAML wrapper in the isolated migration workspace.

- [ ] Write `test_maven_and_gradle_exact_dependency_options`, `test_unknown_options_rejected`, `test_dynamic_or_unowned_target_rejected`, `test_yaml_values_not_shell_code`, `test_same_recipe_different_coordinates_different_identity`. Test exact selected group/artifact and version, wrapper serialization, cleanup, and unchanged legacy no-options invocation.
- [ ] Run new tests and `tests/test_openrewrite.py`; expect new functionality failures.
- [ ] Implement allowlisted inspected Maven/Gradle upgrade recipe options and wrapper execution. Resolve/pin recipe artifacts; reject RELEASE/LATEST artifact fallback for modes. Replacement/removal without supported transformations stays advisory, or blocks if mandatory.
- [ ] Rerun and commit.

## Task 6: Parent linkage and backward-compatible migration recovery

**Files:** Modify `models.py`, `planning_artifacts.py`, `migrations/execution_state.py`, `migrations/run_store.py` and migration orchestration consumers; create `tests/test_modernization_migration_linkage.py`; extend `tests/test_migration_recovery.py`, `tests/test_migration_run_store.py`.

**Interfaces:** Add optional `modernization_plan_id` and `modernization_plan_digest` to `PersistedMigrationPlan`, `RunReport`, `MigrationRun` and `MigrationRequest`. All new mode children carry both. Existing RunManifest embeds the linked plan; hash legacy plans exactly as before when new fields/options are absent. New mode manifests hash the complete linked plan and options.

- [ ] Write `test_legacy_manifest_digest_unchanged` using a prechange serialized manifest/receipt fixture; assert old receipts resume. Write `test_parent_digest_and_options_bound`, `test_wrong_parent_rejected_before_apply`, `test_mode_history_scoped_to_revision_and_objective`.
- [ ] Run these tests plus existing run-store/recovery tests; expect new assertions failing.
- [ ] Implement linkage through child plan persistence, execution, results and recovery. Use full execution identity for mode deduplication and receipts; do not apply legacy recipe-name history across mode objectives/revisions. Avoid globally changing old manifest serialization.
- [ ] Rerun and commit.

## Task 7: Bounded planning toward saved objectives

**Files:** Create `modernization/batching.py`, `tests/test_modernization_batching.py`; modify `factory.py`, `agents/contracts.py`, `agents/planning.py` additively.

**Interfaces:** `plan_batch(intent: ModernizationIntentPlan, progress: ModernizationProgress, evidence: ProjectEvidence, settings: Settings) -> RunReport`; `ModernizationProgress` defined in Task 1. Allow optional intent context in factory/planning dependencies, leaving legacy calls unchanged.

- [ ] Write `test_batches_preserve_destination`, `test_batch_limit_does_not_truncate_objectives`, `test_injected_prerequisite_revalidated`, `test_no_cross_objective_recipe_suppression`, and `test_unrelated_model_goal_rejected`. Assert only ready executable objectives are eligible, targets fixed across fresh evidence, full identity retained and child linkage present.
- [ ] Run `uv run pytest tests/test_modernization_batching.py tests/test_factory.py tests/test_planning.py --no-cov -q`; expect new tests failing.
- [ ] Implement bounded batches using existing planner and scheduling utilities, with deterministic validation after prerequisite/finalizer insertion and before persistence. Do not silently drop required dependencies when filtering. A model's empty answer is not evidence of completion.
- [ ] Rerun and commit.

## Task 8: Objective progress and resumable mode graph

**Files:** Modify `modernization/progress.py`; create `modernization/runner.py`, `workflows/modernization.py`, `tests/test_modernization_progress.py`, `tests/test_modernization_run.py`.

**Interfaces:** Use the progress models from Task 1. `check_objectives(intent: ModernizationIntentPlan, evidence: ProjectEvidence, receipts: list[StepReceipt]) -> list[ObjectiveProgress]`; `ModernizationRunStore.load/save` stores parent ID/digest, expected revision/fingerprints, execution settings, cycle count, child IDs and objective states atomically. `ModernizationRunner.run(plan_id: str, *, apply: bool, max_cycles: int) -> ModernizationRunResult`; `build_modernization_graph(...)` composes existing `NestedMigration` without modifying legacy profile topology.

- [ ] Write `test_exact_versions_all_modules`, `test_source_requires_verified_receipt`, `test_findings_require_fresh_recheck`, `test_advisory_is_visible_not_completed`. A verified no-change source step qualifies only when its declared predicate proves satisfaction; a green execution alone cannot clear arbitrary findings.
- [ ] Write `test_resume_never_resolves_targets`, `test_external_edits_rejected`, `test_owned_publication_crash_reconciled`, `test_options_change_invalidates_resume`, `test_independent_objectives_continue_with_missing_optional_evidence`, `test_empty_repeated_no_progress_and_cycle_limit_stop`, `test_final_verification_required_even_without_changes`.
- [ ] Write `test_security_requires_fresh_resolved_graph_and_scan`, `test_green_build_does_not_clear_vulnerability`, `test_transitive_vulnerability_persists_after_owner_upgrade`, `test_security_scan_outage_leaves_objective_incomplete`, and `test_new_advisory_is_reported_without_target_drift`. Assert exact target and advisory absence across relevant modules/paths; a disappearing dependency is accepted only when graph collection is complete for those modules. Refresh dependency security evidence after build changes and at final verification, including resumed runs whose cached advisory evidence may be stale.
- [ ] Run the two test modules; expect missing-interface failures.
- [ ] Pin checkpoint identity to repository, plan ID/digest and execution settings. Validate provenance before mutation and at each resume. Reconcile published receipts before accepting expected fingerprint advancement; arbitrary external edits require regenerating intent.
- [ ] Implement discover/recheck → ready-objective batch → nested migration → recheck loop. Refresh dependencies for build changes, CodeGraph for source changes and Sonar for source/build changes; require comparable final scan for finding predicates. Block dependent objectives on missing selected evidence but permit independent work; overall status remains incomplete if any executable objective cannot verify. Stop on migration failure and bounded stagnation/cycles. Require final green build/tests at the final revision.
- [ ] Rerun the new modules plus `tests/test_profile_run.py`, `tests/test_migration_graph.py`, `tests/test_migration_recovery.py`; expect all passing. Commit.

## Task 9: CLI, reporting and integration regression

**Files:** Modify `cli.py`, `README.md`; create `modernization/reporting.py`, `tests/test_modernization_cli.py`; extend `tests/test_workflow_characterization.py` only for intentional additive schemas.

**Interfaces:** `plan` subject defaults to current directory. Add optional `--mode` to `plan`/`run` and `--modernization-plan` to `run`. Parse explicit mode separately from recommended default to detect profile conflicts. `render_intent_summary(plan: ModernizationIntentPlan) -> str` and `render_run_summary(result: ModernizationRunResult) -> str` include targets, reasons, coverage, blocked/deferred objectives and artifact paths.

- [ ] Write CLI matrix tests: target alone chooses recommended; modes require target; explicit mode/profile conflict; named profile plus target keeps legacy behavior; goal-only paths unchanged; positional repository and `--repository` supported; saved-plan conflicting target/mode/profile/goal rejected; matching target/mode accepted; run without apply never calls migrations; saved-plan replay does not call resolver; incomplete exits 1 and usage errors exit 2.
- [ ] Write fake end-to-end tests for Java-only Minimal, major Spring Recommended, degraded Full, saved-plan resume and multi-batch convergence, asserting sources unchanged during planning and original intent bytes unchanged throughout execution.
- [ ] Add fake end-to-end Recommended security tests for a direct patch fix, a transitive fix through a BOM upgrade, an unresolved/no-fix advisory, and unavailable verification. Assert reports distinguish remediated, unresolved, newly detected and unverified findings, include advisory IDs and exact targets, and never imply all security findings were fixed when they were not. Unverified executable objectives exit 1; initially deferred findings remain visible alongside degraded coverage.
- [ ] Run `uv run pytest tests/test_modernization_cli.py tests/test_workflow_characterization.py --no-cov -q`; expect new CLI tests failing.
- [ ] Wire the new runner through a focused helper so existing profile closures stay intact. Document commands from the spec, patch pinning, blocked/degraded meanings, and no manual-edit contract. Keep corpus support deferred.
- [ ] Rerun focused tests and commit.

## Verification and review

- [ ] In the managed worktree, establish baseline with `uv run pytest --no-cov -q`; record pre-existing failures before implementation. Do not reuse or modify the original checkout's virtualenv.
- [ ] After implementation run `uv run pytest --no-cov -q`, `uv run ruff check src tests`, and `uv run mypy src/refinery_ai`. Compare with recorded baseline; address regressions. Live model/network/Sonar integration runs remain optional and separate.
- [ ] Run `git diff --check` and review the complete diff against each spec section. Invoke the required review and branch-completion workflows. Preserve the feature worktree; do not merge or disturb primary checkout without the requested integration step.
- [ ] Report verified checks, remaining limitations, and the branch/worktree location. Do not claim completion based on empty planner output or tests that were not run.

## Self-review

Spec sections map to tasks: policy/CLI 1,9; two-stage planning 4,7,8; compatibility/version sources 2; options 5,6; persistence/resume 1,6,8; completion/enforcement 3,7,8; analysis freshness 2,8; backward compatibility 6,9. Security coverage: evidence retention and candidate checking in Task 2; deterministic remediation selection in Task 4; fresh resolved-graph/OSV verification in Task 8; user-visible results and integration tests in Task 9. Each Review Focus input has named tests. Execution order is Tasks 1 through 9. Implementation proceeded on the current branch under the user authorization recorded above. The following execution record supersedes the original workspace prerequisite.


## Implementation record (2026-11-17)

Implemented the nine task areas in `src/refinery_ai/modernization/`, its LangGraph
workflow, planner adapter, CLI dispatch, and additive migration linkage. The
`tests/test_modernization*.py` modules consolidate the plan's proposed test files.
The focused tests cover immutable storage/digests, policy and exact versions,
module observations, security/BOM evidence, bounded execution, CLI compatibility,
external edits, execution settings, and the real publication crash window.

The fresh review identified important failures; regression tests reproduced and
fixed root-module masking, security candidate truncation/major exclusion, source
finding predicate weakening, final advisory omission, unsafe progress paths, and
publication-before-marker recovery. Declared BOM/direct owner remediation now
uses candidate POM evidence and requires fresh resolved-graph/OSV verification.
Invalid model output gets two validation attempts with feedback, then a saved
blocked destination rather than an executable guess.

Implementation rulings and costs:

- User-authorized current branch replaces the broken worktree prerequisite;
  changes are not isolated from this checkout.
- Deterministic batches schedule the immutable saved transformations and reuse
  the existing migration graph through durable child boundaries. Adaptive recipe
  replanning and a native nested parent checkpoint are deferred; child recovery,
  identity checks and target pinning remain enforced.
- Floating framework composites are excluded. A bounded atomic exact Maven
  parent/BOM plus Jakarta namespace wrapper handles initial framework changes;
  broader API changes can require the existing repair backend or end incomplete.
- Source execution is limited to two audited recipes; other opportunities remain
  advisory. Findings-based predicates retain fresh analyzer checks and verified
  receipt requirements.
- New Gradle mode execution is blocked before mutation because exact artifact
  and JDK verification are unsupported. Legacy named-profile behavior is retained.
- Candidate metadata defaults to Maven Central. Unresolvable private metadata,
  deep inherited/mediated ownership and unsupported replacements remain advisory.
- Fresh analysis is collected at cycle/final verification boundaries rather than
  sharing the legacy dependency-only cache; this costs more scanning but avoids
  stale source-quality completion evidence.

Verification is recorded in the execution ledger and the final response. Live
model, OSV, SonarQube and OpenRewrite migrations are separate from the deterministic
unit/integration suite and have not been represented as tested end to end.

### Verified result

Final suite: **596 passed, 1 skipped** (`pytest --no-cov -q`, proxy environment
variables cleared for the test process to avoid the pre-existing missing-socksio
baseline issue). Mypy: **137 source files clean**. Feature Ruff checks and
`git diff --check` pass. Repository-wide Ruff retains one pre-existing E501 at
`src/refinery_ai/migrations/deterministic_repair.py:1478`; that file was not changed.
Changes remain uncommitted on `main` as requested; no merge or push was performed.
