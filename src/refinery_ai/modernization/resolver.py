"""Resolve a destination once, ground it deterministically, and persist it."""

import json
from pathlib import Path
from typing import Protocol

from pydantic import Field

from refinery_ai.config import Settings
from refinery_ai.modernization.compatibility import CompatibilityProvider
from refinery_ai.modernization.evidence import ProjectEvidence, collect_evidence
from refinery_ai.modernization.models import (
    CompletionPredicate,
    Contract,
    EvidenceReference,
    ModernizationIntentPlan,
    ModernizationMode,
    ModernizationObjective,
    ObjectiveCategory,
    TargetSelection,
    TransformationSpec,
)
from refinery_ai.modernization.policy import mode_policy
from refinery_ai.modernization.security import owner_remediation, remediation_candidates
from refinery_ai.modernization.store import IntentPlanStore
from refinery_ai.modernization.validation import (
    SOURCE_RECIPES,
    validate_intent,
    validate_transformation_scope,
)
from refinery_ai.modernization.versions import VersionCandidateProvider
from refinery_ai.recipes.catalog import RecipeCatalogSession
from refinery_ai.recipes.sources import RecipeSource, fetch_recipe_sources


class DestinationChoices(Contract):
    spring_boot_version: str | None = None
    rationale: str
    objectives: list[ModernizationObjective] = Field(default_factory=list)


class IntentResolutionRequest(Contract):
    evidence: ProjectEvidence
    mode: ModernizationMode
    requested_jdk: int
    framework_candidates: list[TargetSelection]
    goal: str | None = None
    validation_feedback: str | None = None


class DestinationPlanner(Protocol):
    def resolve(self, request: IntentResolutionRequest) -> DestinationChoices: ...


def java_major(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.split(".")[1] if value.startswith("1.") else value.split(".")[0])
    except (ValueError, IndexError):
        return None


def _resolve_intent_once(
    repository: Path,
    target_java: int,
    mode: ModernizationMode | str,
    settings: Settings,
    *,
    planner: DestinationPlanner | None = None,
    evidence: ProjectEvidence | None = None,
    catalog: RecipeCatalogSession | None = None,
    refresh_recipe_catalog: bool = False,
    goal: str | None = None,
    validation_feedback: str | None = None,
) -> ModernizationIntentPlan:
    root = repository.expanduser().resolve()
    mode = ModernizationMode(mode)
    if target_java < 8:
        raise ValueError("Target JDK must be at least 8")
    evidence = evidence or collect_evidence(root, mode_policy(mode), settings)
    for observed in evidence.module_versions.values():
        current = java_major(observed.get("java"))
        if current and current > target_java:
            raise ValueError("Java downgrade is unsupported; choose an equal or higher target")
    if catalog is None:
        sources = fetch_recipe_sources(
            timeout=settings.recipe_catalog_timeout_seconds,
            refresh=refresh_recipe_catalog,
            verify_ssl=settings.recipe_catalog_verify_ssl,
            cache_directory=root / settings.state_directory / "cache/recipe-sources",
        )
        bundled = json.loads((Path(__file__).parent / "data/core-recipes.json").read_text())
        sources.extend(
            RecipeSource.model_validate(
                {
                    k: v
                    for k, v in item.items()
                    if k not in {"options_metadata", "descriptor_digest"}
                }
            )
            for item in bundled
        )
        catalog = RecipeCatalogSession(sources)
    compatibility = CompatibilityProvider()
    references = list(evidence.references)
    for entry in compatibility.entries:
        references.append(
            EvidenceReference(
                id=f"compatibility-{entry['version']}",
                source=entry["source"],
                detail=f"Java {entry['java_min']}-{entry['java_max']}; "
                f"verified {entry['verified']}; exact Spring Boot {entry['version']}",
            )
        )
    request = IntentResolutionRequest(
        evidence=evidence,
        goal=goal,
        validation_feedback=validation_feedback,
        mode=mode,
        requested_jdk=target_java,
        framework_candidates=compatibility.candidates(target_java)
        if evidence.snapshot.spring_boot_version
        else [],
    )
    if planner is None:
        from refinery_ai.agents.modernization import LangGraphDestinationPlanner

        planner = LangGraphDestinationPlanner(settings, catalog)
    choices = planner.resolve(request)
    current_boot = evidence.snapshot.spring_boot_version
    selected_boot = choices.spring_boot_version
    if selected_boot and not current_boot:
        raise ValueError("Cannot add Spring Boot to a non-Spring project")
    if selected_boot and not compatibility.supports(selected_boot, target_java):
        raise ValueError("Spring Boot destination lacks verified JDK compatibility")
    if (
        mode is ModernizationMode.MINIMAL
        and current_boot
        and compatibility.supports(current_boot, target_java)
    ):
        if selected_boot and selected_boot != current_boot:
            raise ValueError("Minimal must retain a compatible framework")
        selected_boot = current_boot
    elif mode is ModernizationMode.MINIMAL and current_boot:
        # Unknown current compatibility is not permission for an optional major jump.
        def numeric_version(value: str) -> tuple[int, ...]:
            return tuple(int(part) for part in value.split("."))

        minimal_candidates = [
            t
            for t in request.framework_candidates
            if numeric_version(t.version) >= numeric_version(current_boot)
        ]
        if minimal_candidates:
            selected_boot = min(
                minimal_candidates, key=lambda t: numeric_version(t.version)
            ).version
            choices.rationale = (
                "Smallest sourced framework destination compatible with the requested JDK"
            )
    targets = [
        TargetSelection(
            component="java",
            version=str(target_java),
            evidence_ids=["discovery"],
            rationale="Requested JDK",
        )
    ]
    objectives: list[ModernizationObjective] = []
    blocked: list[str] = []
    if evidence.snapshot.build_system.value == "gradle":
        blocked.append(
            "Gradle mode execution is not supported until recipe artifact "
            "pinning and exact module JDK discovery are available; named "
            "profiles remain supported"
        )
    limitations = list(evidence.limitations)

    def transformation(
        recipe: str,
        category: ObjectiveCategory = "compatibility",
        options: dict[str, str | int | bool] | None = None,
    ) -> TransformationSpec:
        if catalog is None or catalog.get(recipe) is None:
            raise ValueError(f"No inspected transformation: {recipe}")
        artifact = catalog.recipe_artifacts[recipe]
        step = TransformationSpec(
            recipe=recipe, artifact=artifact, options=options or {}, effects=[category]
        )
        validate_transformation_scope(step, mode, catalog)
        return step

    java_recipe = f"org.openrewrite.java.migrate.UpgradeBuildToJava{target_java}"
    java_steps: list[TransformationSpec] = []
    if any(java_major(v.get("java")) != target_java for v in evidence.module_versions.values()):
        try:
            java_steps.append(transformation(java_recipe))
        except ValueError as error:
            blocked.append(str(error))
    objectives.append(
        ModernizationObjective(
            id="java",
            category="compatibility",
            rationale="Reach the requested JDK in every module",
            evidence_ids=["discovery"],
            target=targets[0],
            required=True,
            transformations=java_steps,
            completion=CompletionPredicate(
                kind="version", component="java", version=str(target_java)
            ),
        )
    )
    if current_boot:
        selected_boot = selected_boot or current_boot
        if not compatibility.supports(selected_boot, target_java):
            blocked.append(
                f"Compatibility of Spring Boot {selected_boot} and Java {target_java} is unverified"
            )
        target = TargetSelection(
            component="spring_boot",
            version=selected_boot,
            evidence_ids=[f"compatibility-{selected_boot}"]
            if compatibility.supports(selected_boot, target_java)
            else ["discovery"],
            rationale=choices.rationale,
        )
        targets.append(target)
        steps = []
        if selected_boot != current_boot:
            selected_entry = next(
                (e for e in compatibility.entries if e["version"] == selected_boot), None
            )
            try:
                if selected_entry is None:
                    raise ValueError("No framework migration known")
                framework_owner = next(
                    (
                        name
                        for name in ("spring-boot-starter-parent", "spring-boot-dependencies")
                        if evidence.owners.get(f"org.springframework.boot:{name}")
                    ),
                    None,
                )
                if framework_owner is None or evidence.snapshot.build_system.value != "maven":
                    raise ValueError("Exact framework patch owner is not resolved for this build")
                steps.append(
                    transformation(
                        "refinery.UpgradeSpringBoot",
                        options={
                            "groupId": "org.springframework.boot",
                            "artifactId": framework_owner,
                            "newVersion": selected_boot,
                            "migrateJakarta": current_boot.startswith("2.")
                            and selected_boot.startswith("3."),
                        },
                    )
                )
                limitations.append(
                    "Framework migration uses an exact owner upgrade and bounded "
                    "Jakarta namespace changes; additional API changes must pass "
                    "isolated repair/build/test verification"
                )
            except ValueError as error:
                blocked.append(str(error))
        objectives.append(
            ModernizationObjective(
                id="spring-boot",
                category="compatibility",
                rationale=choices.rationale,
                evidence_ids=target.evidence_ids,
                target=target,
                required=True,
                transformations=steps,
                completion=CompletionPredicate(
                    kind="version", component="spring_boot", version=selected_boot
                ),
            )
        )
    # Additional objectives cannot invent versions or targets: dependency/security work is
    # generated below from structured advisories, while source work must cite collected evidence.
    evidence_ids = {ref.id for ref in references}
    for objective in choices.objectives:
        if objective.category != "source":
            raise ValueError(
                f"Objective {objective.id!r} has category {objective.category!r}; "
                "additional objectives must have category 'source'. Java, framework, "
                "dependency and security objectives are generated by the resolver."
            )
        unknown_evidence = set(objective.evidence_ids) - evidence_ids
        if unknown_evidence:
            raise ValueError(
                f"Objective {objective.id!r} cites unknown evidence IDs: "
                f"{sorted(unknown_evidence)}. Use evidence reference IDs from "
                f"{sorted(evidence_ids)}; finding rule IDs belong in "
                "completion.finding_ids, not evidence_ids."
            )
        if mode is ModernizationMode.MINIMAL:
            raise ValueError("Minimal excludes optional source objectives")
        supported = (
            bool(objective.transformations)
            and objective.target is None
            and all(
                step.recipe in SOURCE_RECIPES
                and SOURCE_RECIPES[step.recipe] <= target_java
                and not step.options
                and catalog.get(step.recipe) is not None
                and step.artifact == catalog.recipe_artifacts[step.recipe]
                for step in objective.transformations
            )
        )
        if objective.completion.kind == "finding":
            supported = (
                supported
                and any(
                    a.analyzer == objective.completion.analyzer and a.status == "completed"
                    for a in evidence.analysis
                )
                and set(objective.completion.finding_ids)
                <= set(evidence.findings.get(objective.completion.analyzer or "", []))
            )
        if not supported:
            objective = objective.model_copy(
                update={
                    "disposition": "advisory",
                    "transformations": [],
                    "target": None,
                    "required": False,
                    "rationale": objective.rationale
                    + "; source effects or target JDK are not verified",
                    "completion": CompletionPredicate(kind="receipt"),
                }
            )
            limitations.append(f"{objective.id}: unsupported source transformation deferred")
        else:
            objective = objective.model_copy(
                update={
                    "depends_on": list(dict.fromkeys(["java", *objective.depends_on])),
                    "completion": objective.completion
                    if objective.completion.kind == "finding"
                    else CompletionPredicate(kind="receipt", no_change_proves_satisfied=True),
                    "transformations": [
                        step.model_copy(update={"effects": ["source"]})
                        for step in objective.transformations
                    ],
                }
            )
        objectives.append(objective)
    if mode_policy(mode).security:
        from refinery_ai.inspectors.dependencies.osv import OsvClient

        scanner = OsvClient(
            settings.osv_api_url,
            settings.osv_timeout_seconds,
            settings.osv_batch_size,
            verify_ssl=settings.osv_verify_ssl,
        )
        versions = VersionCandidateProvider(root / settings.state_directory / "cache/versions")
        declared = {(d.group, d.artifact): d for d in evidence.snapshot.dependencies}
        current_managed: dict[tuple[str, str], str] = {}
        selected_managed: dict[tuple[str, str], str] = {}
        if (
            current_boot
            and selected_boot
            and selected_boot != current_boot
            and evidence.owners
            and evidence.vulnerabilities
        ):
            current_managed = versions.managed_versions(
                "org.springframework.boot", "spring-boot-dependencies", current_boot
            )
            selected_managed = versions.managed_versions(
                "org.springframework.boot", "spring-boot-dependencies", selected_boot
            )
        for index, (purl, vulnerabilities) in enumerate(sorted(evidence.vulnerabilities.items())):
            active = [v for v in vulnerabilities if not v.get("withdrawn")]
            if not active:
                continue
            dependency = next(
                (
                    d
                    for d in evidence.inventory
                    if purl == f"pkg:maven/{d['group']}/{d['artifact']}@{d['version']}"
                ),
                None,
            )
            reference_id = f"security-{index}"
            references.append(EvidenceReference(id=reference_id, source=purl, detail=str(active)))
            ids = sorted({v["id"] for v in active})
            rationale = f"Address {', '.join(ids)} in {purl}"
            security_target: TargetSelection | None = None
            steps = []
            reason = "No verified direct dependency owner and supported fix"
            dependencies: list[str] = []
            verified_owner_fix = False
            if dependency and dependency.get("direct"):
                declaration = declared.get((dependency["group"], dependency["artifact"]))
                # Versionless/BOM-managed and property references require owner resolution;
                # leave them visible instead of breaking dependency management with an override.
                owner = evidence.owners.get(f"{dependency['group']}:{dependency['artifact']}", {})
                if owner.get("version") == dependency["version"] or (
                    declaration and declaration.version == dependency["version"]
                ):
                    try:
                        candidates = remediation_candidates(
                            dependency["group"],
                            dependency["artifact"],
                            dependency["version"],
                            versions,
                            scanner,
                        )
                        if candidates:
                            candidate = candidates[0]
                            references.append(
                                EvidenceReference(
                                    id=f"{reference_id}-version",
                                    source=candidate.source,
                                    detail=(
                                        f"{candidate.version}; retrieved {candidate.retrieved_at}; "
                                        f"cached={candidate.cached}"
                                    ),
                                )
                            )
                            security_target = TargetSelection(
                                component="dependency",
                                group=dependency["group"],
                                artifact=dependency["artifact"],
                                version=candidate.version,
                                evidence_ids=[reference_id],
                                rationale=rationale,
                            )
                            recipe = (
                                "org.openrewrite.maven.UpgradeDependencyVersion"
                                if evidence.snapshot.build_system.value == "maven"
                                else "org.openrewrite.gradle.UpgradeDependencyVersion"
                            )
                            steps = [
                                transformation(
                                    recipe,
                                    "security",
                                    {
                                        "groupId": dependency["group"],
                                        "artifactId": dependency["artifact"],
                                        "newVersion": security_target.version,
                                    },
                                )
                            ]
                            reason = (
                                "Compatibility beyond declared constraints requires isolated "
                                "build/tests"
                            )
                    except (ValueError, OSError, RuntimeError) as error:
                        reason = str(error)
            if dependency and not steps and selected_boot and not blocked:
                key = (dependency["group"], dependency["artifact"])
                declaration = declared.get(key)
                candidate_version = selected_managed.get(key)
                if (
                    candidate_version
                    and current_managed.get(key) == dependency["version"]
                    and (declaration is None or declaration.version is None)
                ):
                    candidate_purl = f"pkg:maven/{key[0]}/{key[1]}@{candidate_version}"
                    try:
                        checked = scanner.query([candidate_purl])
                        if candidate_purl in checked and not any(
                            not v.withdrawn for v in checked[candidate_purl]
                        ):
                            security_target = TargetSelection(
                                component="spring_boot",
                                version=selected_boot,
                                evidence_ids=[reference_id],
                                rationale="Upgrade the owning Spring Boot dependency set",
                            )
                            dependencies = ["spring-boot"]
                            verified_owner_fix = True
                            reason = (
                                f"Chosen framework BOM resolves this package to "
                                f"{candidate_version}; recheck resolved graph after migration"
                            )
                    except (ValueError, OSError, RuntimeError) as error:
                        reason = str(error)
            if (
                dependency
                and not steps
                and not verified_owner_fix
                and evidence.snapshot.build_system.value == "maven"
            ):
                try:
                    owner_fix = owner_remediation(
                        dependency,
                        evidence.owners,
                        versions,
                        scanner,
                        {
                            f"{t.group}:{t.artifact}": t.version
                            for t in targets
                            if t.component == "dependency"
                        },
                    )
                    if owner_fix:
                        coordinate, owner_candidate, fixed_version = owner_fix
                        group, artifact = coordinate.split(":", 1)
                        references.append(
                            EvidenceReference(
                                id=f"{reference_id}-owner",
                                source=owner_candidate.source,
                                detail=(
                                    f"Owner {coordinate}@{owner_candidate.version} "
                                    "resolves affected "
                                    f"package to {fixed_version}; candidate OSV query clean; "
                                    "retrieved "
                                    f"{owner_candidate.retrieved_at}"
                                ),
                            )
                        )
                        security_target = TargetSelection(
                            component="dependency",
                            group=group,
                            artifact=artifact,
                            version=owner_candidate.version,
                            evidence_ids=[reference_id, f"{reference_id}-owner"],
                            rationale="Remediate the transitive package through its declared owner",
                        )
                        steps = [
                            transformation(
                                "org.openrewrite.maven.UpgradeDependencyVersion",
                                "security",
                                {
                                    "groupId": group,
                                    "artifactId": artifact,
                                    "newVersion": owner_candidate.version,
                                },
                            )
                        ]
                        reason = (
                            "Owner candidate resolves a verified fixed package; confirm "
                            "mediation in fresh graph and build/tests"
                        )
                except (OSError, RuntimeError, ValueError) as error:
                    reason = str(error)
            executable = bool(steps) or verified_owner_fix
            if security_target and executable:
                targets.append(security_target)
            objectives.append(
                ModernizationObjective(
                    id=f"security-{index}",
                    category="security",
                    rationale=f"{rationale}. {reason}",
                    evidence_ids=[reference_id],
                    target=security_target,
                    transformations=steps,
                    depends_on=dependencies,
                    disposition="executable" if executable else "advisory",
                    advisory_ids=ids,
                    affected_coordinates=[purl],
                    completion=CompletionPredicate(
                        kind="finding", analyzer="dependencies", finding_ids=ids
                    ),
                )
            )
            if not executable:
                limitations.append(f"{rationale}: {reason}")
        # Maintenance upgrades are deliberately narrower than security fixes: latest
        # verified patch in the current minor line, preserving declared/BOM ownership.
        secured = {
            p for o in objectives if o.category == "security" for p in o.affected_coordinates
        }
        seen_dependencies: set[tuple[str, str]] = set()
        for dependency in evidence.inventory:
            key = (dependency["group"], dependency["artifact"])
            purl = f"pkg:maven/{key[0]}/{key[1]}@{dependency['version']}"
            declaration = declared.get(key)
            owner = evidence.owners.get(":".join(key), {})
            if (
                not dependency.get("direct")
                or key in seen_dependencies
                or purl in secured
                or any(t.group == key[0] and t.artifact == key[1] for t in targets)
                or key[0] == "org.springframework.boot"
                or not (
                    owner.get("version") == dependency["version"]
                    or (declaration and declaration.version == dependency["version"])
                )
            ):
                continue
            seen_dependencies.add(key)
            try:
                candidates = remediation_candidates(*key, dependency["version"], versions, scanner)
                candidates = [
                    c
                    for c in candidates
                    if c.version.split(".")[:2] == dependency["version"].split(".")[:2]
                ]
                if not candidates:
                    continue
                selected = candidates[-1]
                reference_id = f"maintenance-{len(seen_dependencies)}"
                references.append(
                    EvidenceReference(
                        id=reference_id,
                        source=selected.source,
                        detail=f"{selected.version}; retrieved {selected.retrieved_at}; "
                        f"cached={selected.cached}; candidate OSV query found no active advisories",
                    )
                )
                target = TargetSelection(
                    component="dependency",
                    group=key[0],
                    artifact=key[1],
                    version=selected.version,
                    evidence_ids=[reference_id],
                    rationale="Maintain the declared dependency within its existing minor line",
                )
                recipe = (
                    "org.openrewrite.maven.UpgradeDependencyVersion"
                    if evidence.snapshot.build_system.value == "maven"
                    else "org.openrewrite.gradle.UpgradeDependencyVersion"
                )
                step = transformation(
                    recipe,
                    "dependency",
                    {
                        "groupId": key[0],
                        "artifactId": key[1],
                        "newVersion": selected.version,
                    },
                )
                targets.append(target)
                objectives.append(
                    ModernizationObjective(
                        id=reference_id,
                        category="dependency",
                        rationale=target.rationale,
                        evidence_ids=[reference_id],
                        target=target,
                        transformations=[step],
                        completion=CompletionPredicate(
                            kind="version", component="dependency", version=target.version
                        ),
                    )
                )
            except (OSError, RuntimeError, ValueError) as error:
                limitations.append(f"Maintenance upgrade {key[0]}:{key[1]} unresolved: {error}")
    plan = ModernizationIntentPlan(
        repository=root,
        source_revision=evidence.revision,
        build_fingerprint=evidence.build_fingerprint,
        source_fingerprint=evidence.source_fingerprint,
        requested_jdk=target_java,
        mode=mode,
        targets=targets,
        evidence=references,
        analysis=evidence.analysis,
        assumptions=[f"User goal: {goal}"] if goal else [],
        limitations=limitations,
        blocked_reasons=blocked,
        objectives=objectives,
    )
    validate_intent(plan, catalog)
    IntentPlanStore(root, settings.state_directory).save(plan)
    return plan


def resolve_intent(
    repository: Path,
    target_java: int,
    mode: ModernizationMode | str,
    settings: Settings,
    *,
    planner: DestinationPlanner | None = None,
    evidence: ProjectEvidence | None = None,
    catalog: RecipeCatalogSession | None = None,
    refresh_recipe_catalog: bool = False,
    goal: str | None = None,
) -> ModernizationIntentPlan:
    """Bound model validation retries; persist a blocked destination if both fail."""
    root = repository.expanduser().resolve()
    mode = ModernizationMode(mode)
    if target_java < 8:
        raise ValueError("Target JDK must be at least 8")
    evidence = evidence or collect_evidence(root, mode_policy(mode), settings)
    if any(
        (java_major(v.get("java")) or 0) > target_java for v in evidence.module_versions.values()
    ):
        raise ValueError("Java downgrade is unsupported; choose an equal or higher target")
    failure: str | None = None
    for attempt in range(2):
        try:
            return _resolve_intent_once(
                root,
                target_java,
                mode,
                settings,
                planner=planner,
                evidence=evidence,
                catalog=catalog,
                refresh_recipe_catalog=refresh_recipe_catalog and attempt == 0,
                goal=goal,
                validation_feedback=failure,
            )
        except ValueError as error:
            failure = str(error)
    target = TargetSelection(
        component="java",
        version=str(target_java),
        evidence_ids=["discovery"],
        rationale="Requested JDK",
    )
    blocked = ModernizationIntentPlan(
        repository=root,
        source_revision=evidence.revision,
        build_fingerprint=evidence.build_fingerprint,
        source_fingerprint=evidence.source_fingerprint,
        requested_jdk=target_java,
        mode=mode,
        targets=[target],
        evidence=evidence.references,
        analysis=evidence.analysis,
        limitations=evidence.limitations,
        blocked_reasons=[f"Destination validation failed after two attempts: {failure}"],
        objectives=[
            ModernizationObjective(
                id="java",
                category="compatibility",
                required=True,
                disposition="advisory",
                rationale="Resolve a valid destination before migration",
                evidence_ids=["discovery"],
                target=target,
                completion=CompletionPredicate(
                    kind="version", component="java", version=str(target_java)
                ),
            )
        ],
    )
    IntentPlanStore(root, settings.state_directory).save(blocked)
    return blocked
