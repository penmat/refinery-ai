"""Bounded AI repair for deterministic migrations with semantic leftovers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field, TypeAdapter

from refinery_ai.config import Settings
from refinery_ai.llm import run_tool_loop
from refinery_ai.models import RecipeRecommendation, RepairMode, VerificationResult

_SPRINGFOX_MARKERS = ("springfox.", "transformation of Docket", "Migrate manually")
_MISSING_SWAGGER_1_ANNOTATIONS = "package io.swagger.annotations does not exist"
_MISSING_GENERATED_JAVAX_APIS = (
    "package javax.validation does not exist",
    "package javax.annotation does not exist",
)
_BOOT3_VALIDATION_FAILURE = "Status expected:<400> but was:<201>"
_HIBERNATE6_QUERY_FAILURE = "InvalidDataAccessApiUsage"
_JDBC_QUERY_FAILURE = "BadSqlGrammar"


def _has_legacy_swagger_annotations(content: str) -> bool:
    return bool(
        re.search(
            r"<groupId>\s*io\.swagger\s*</groupId>\s*"
            r"<artifactId>\s*swagger-annotations\s*</artifactId>",
            content,
        )
    )


class RepairFileReplacement(BaseModel):
    path: str
    content: str


class RepairEditProposal(BaseModel):
    diagnosis: str
    summary: str
    files: list[RepairFileReplacement] = Field(min_length=1)


class MigrationRepairer(Protocol):
    def repair(
        self,
        repository: Path,
        step: RecipeRecommendation,
        failure: VerificationResult,
    ) -> VerificationResult: ...


def springdoc_repair_files(repository: Path) -> tuple[Path, ...]:
    """Return only files containing residual Springfox migration markers."""

    files: list[Path] = []
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue
        if any(marker in content for marker in _SPRINGFOX_MARKERS):
            files.append(relative)
    return tuple(sorted(files))


def configure_openapi_generator_for_springdoc(
    repository: Path, failure: VerificationResult
) -> bool:
    """Align generated Spring models with Springdoc and the active Boot generation."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    needs_boot3_generator = any(
        marker in diagnostic for marker in _MISSING_GENERATED_JAVAX_APIS
    )
    if _MISSING_SWAGGER_1_ANNOTATIONS not in diagnostic and not needs_boot3_generator:
        return False
    pom = repository / "pom.xml"
    if not pom.is_file():
        return False
    content = pom.read_text(errors="replace")
    if "openapi-generator-maven-plugin" not in content:
        return False
    if needs_boot3_generator:
        updated = re.sub(
            r"(<openapi-generator-maven-plugin\.version>)[^<]+(</openapi-generator-maven-plugin\.version>)",
            r"\g<1>6.6.0\g<2>",
            content,
            count=1,
        )
        if "<useSpringBoot3>" not in updated:
            updated = re.sub(
                r"^(?P<indent>[ \t]*)<configOptions>[ \t]*$",
                lambda match: (
                    f"{match.group('indent')}<configOptions>\n"
                    f"{match.group('indent')}    <useSpringBoot3>true</useSpringBoot3>"
                ),
                updated,
                count=1,
                flags=re.MULTILINE,
            )
        if updated == content:
            return False
        pom.write_text(updated)
        return True
    if _has_legacy_swagger_annotations(content):
        return False
    dependencies_end = content.find("</dependencies>")
    if dependencies_end < 0:
        return False
    dependency = (
        "        <dependency>\n"
        "            <groupId>io.swagger</groupId>\n"
        "            <artifactId>swagger-annotations</artifactId>\n"
        "            <version>1.6.2</version>\n"
        "        </dependency>\n"
    )
    updated = content[:dependencies_end] + dependency + content[dependencies_end:]
    pom.write_text(updated)
    return True


def configure_boot3_test_compatibility(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Apply narrow compatibility fixes exposed only after a Boot 3 build compiles."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    repairs: list[str] = []
    pom = repository / "pom.xml"
    if _BOOT3_VALIDATION_FAILURE in diagnostic and pom.is_file():
        content = pom.read_text(errors="replace")
        if "openapi-generator-maven-plugin" in content and "<useBeanValidation>" not in content:
            updated = re.sub(
                r"^(?P<indent>[ \t]*)<configOptions>[ \t]*$",
                lambda match: (
                    f"{match.group('indent')}<configOptions>\n"
                    f"{match.group('indent')}    <useBeanValidation>true</useBeanValidation>"
                ),
                content,
                count=1,
                flags=re.MULTILINE,
            )
            if updated != content:
                pom.write_text(updated)
                repairs.append("enabled Jakarta Bean Validation in generated OpenAPI DTOs")
        repairs.extend(_require_nonempty_openapi_fields(repository, diagnostic))

    if _HIBERNATE6_QUERY_FAILURE in diagnostic:
        replacements = {
            "WHERE pet_id=": "WHERE visit.pet.id=",
            "WHERE type_id=": "WHERE pet.type.id=",
        }
        jpa_roots = (
            repository / "src/main/java",
        )
        paths = (
            path
            for root in jpa_roots
            for path in root.glob("**/*RepositoryImpl.java")
            if any(
                part.lower().endswith("jpa")
                for part in path.relative_to(root).parts[:-1]
            )
        )
        for path in paths:
            content = path.read_text(errors="replace")
            updated = content
            for legacy, entity_path in replacements.items():
                updated = updated.replace(legacy, entity_path)
            if updated != content:
                path.write_text(updated)
                repairs.append(f"updated Hibernate 6 JPQL in {path.relative_to(repository)}")
    if _JDBC_QUERY_FAILURE in diagnostic:
        replacements = {
            "WHERE visit.pet.id=": "WHERE pet_id=",
            "WHERE pet.type.id=": "WHERE type_id=",
        }
        for path in repository.glob("src/main/java/**/jdbc/*RepositoryImpl.java"):
            content = path.read_text(errors="replace")
            updated = content
            for entity_path, column in replacements.items():
                updated = updated.replace(entity_path, column)
            if updated != content:
                path.write_text(updated)
                repairs.append(f"restored JDBC SQL in {path.relative_to(repository)}")
    return tuple(repairs)


def _require_nonempty_openapi_fields(
    repository: Path, diagnostic: str
) -> tuple[str, ...]:
    """Make non-empty request fields explicit when newer generators honor readOnly."""

    controllers = set(re.findall(r"([A-Z][A-Za-z0-9]+)RestControllerTests", diagnostic))
    repairs: list[str] = []
    for path in sorted((repository / "src/main/resources").glob("*.y*ml")):
        content = path.read_text(errors="replace")
        updated = content
        for schema in controllers:
            pattern = re.compile(
                rf"(?ms)^(?P<indent>[ ]+){re.escape(schema)}:\n"
                rf"(?P<body>.*?)(?=^(?P=indent)[A-Za-z0-9_.-]+:\n|\Z)"
            )
            match = pattern.search(updated)
            if match is None:
                continue
            body = match.group("body")
            candidates = re.findall(
                r"(?ms)^ {8}([A-Za-z0-9_.-]+):\n"
                r"(?:(?!^ {8}[A-Za-z0-9_.-]+:).)*?^ {10}minLength: [1-9][0-9]*$",
                body,
            )
            required = set(re.findall(r"(?m)^ {8}- ([A-Za-z0-9_.-]+)$", body))
            missing = [candidate for candidate in candidates if candidate not in required]
            if not missing or "      required:\n" not in body:
                continue
            insertion = "".join(f"        - {candidate}\n" for candidate in missing)
            body = re.sub(
                r"(?m)(^      required:\n(?:        - [^\n]+\n)+)",
                rf"\g<1>{insertion}",
                body,
                count=1,
            )
            updated = updated[: match.start("body")] + body + updated[match.end("body") :]
            repairs.append(
                f"required non-empty {schema} fields in {path.relative_to(repository)}"
            )
        if updated != content:
            path.write_text(updated)
    return tuple(repairs)


def springdoc_repair_applicable(
    repository: Path, failure: VerificationResult
) -> bool:
    """Return whether a failed build has a known, scoped Springdoc repair."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    pom = repository / "pom.xml"
    generated_annotation_failure = (
        (
            _MISSING_SWAGGER_1_ANNOTATIONS in diagnostic
            or any(marker in diagnostic for marker in _MISSING_GENERATED_JAVAX_APIS)
        )
        and pom.is_file()
        and "openapi-generator-maven-plugin" in pom.read_text(errors="replace")
        and (
            any(marker in diagnostic for marker in _MISSING_GENERATED_JAVAX_APIS)
            or not _has_legacy_swagger_annotations(pom.read_text(errors="replace"))
        )
    )
    boot3_test_failure = (
        _BOOT3_VALIDATION_FAILURE in diagnostic
        or _HIBERNATE6_QUERY_FAILURE in diagnostic
        or _JDBC_QUERY_FAILURE in diagnostic
    )
    return (
        bool(springdoc_repair_files(repository))
        or generated_annotation_failure
        or boot3_test_failure
    )


class AiMigrationRepairer:
    """Ask a configured model for validated, scoped source replacements."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.repair_mode is RepairMode.LOCAL:
            _require_local_endpoint(settings.litellm_api_base)

    def repair(
        self,
        repository: Path,
        step: RecipeRecommendation,
        failure: VerificationResult,
    ) -> VerificationResult:
        generator_repaired = configure_openapi_generator_for_springdoc(repository, failure)
        boot3_repairs = configure_boot3_test_compatibility(repository, failure)
        allowed = springdoc_repair_files(repository)
        if not allowed:
            if generator_repaired or boot3_repairs:
                details = "; ".join(boot3_repairs)
                return _result(
                    0,
                    stdout=(
                        "Updated OpenAPI Generator compatibility for the migrated documentation "
                        "and framework stack"
                        + (f"; {details}" if details else "")
                    ),
                )
            return _result(1, "No residual Springfox source files were found")
        context = []
        for relative in allowed:
            content = (repository / relative).read_text(errors="replace")
            context.append(f"--- {relative}\n{content[: self.settings.max_output_characters]}")
        pom = repository / "pom.xml"
        if pom.is_file():
            context.append(f"--- pom.xml\n{pom.read_text(errors='replace')[:10000]}")
        prompt = (
            "Return only JSON matching this schema: "
            f"{json.dumps(RepairEditProposal.model_json_schema())}\n"
            "Return complete replacement content for only the files that must change to finish "
            "the Springfox Docket to "
            "Springdoc conversion. Preserve API grouping and authorization behavior. Do not "
            "disable checks or edit files outside Allowed files.\n"
            f"Recipe: {step.recipe}\nAllowed files: {[str(path) for path in allowed]}\n"
            f"Build failure:\n{failure.model_dump_json()}\nSources:\n" + "\n".join(context)
        )
        try:
            response = run_tool_loop(
                [
                    {
                        "role": "system",
                        "prompt": "You repair Java migrations with minimal patches.",
                    },
                    {"role": "user", "prompt": prompt},
                ],
                max_turns=1,
                settings=self.settings,
            )
            proposal = TypeAdapter(RepairEditProposal).validate_json(
                _json_payload(response.content or "")
            )
        except Exception as error:
            return _result(1, f"Repair model failed or returned invalid JSON: {error}")
        allowed_names = {str(path) for path in allowed}
        changed = [replacement.path for replacement in proposal.files]
        if len(changed) != len(set(changed)) or set(changed) - allowed_names:
            return _result(1, "Repair proposal attempted files outside the allowed scope")
        for replacement in proposal.files:
            target = (repository / replacement.path).resolve()
            if not target.is_relative_to(repository.resolve()) or not target.is_file():
                return _result(1, f"Unsafe or missing repair target: {replacement.path}")
        for replacement in proposal.files:
            (repository / replacement.path).write_text(replacement.content)
        summary = proposal.summary
        if generator_repaired:
            summary = (
                "Preserved generated DTO annotation compatibility. " + summary
            )
        return _result(0, stdout=summary)


def _require_local_endpoint(api_base: str | None) -> None:
    if not api_base:
        raise ValueError("Local repair mode requires REFINERY_LITELLM_API_BASE")
    host = urlparse(api_base).hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Local repair mode requires a loopback model endpoint")


def _json_payload(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        return stripped[7:-3].strip()
    return stripped


def _result(exit_code: int, stderr: str = "", stdout: str = "") -> VerificationResult:
    return VerificationResult(
        command=["refinery", "ai-repair"],
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0,
    )
