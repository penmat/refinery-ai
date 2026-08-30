"""Bounded AI repair for deterministic migrations with semantic leftovers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field, TypeAdapter

from refinery_ai.config import Settings
from refinery_ai.llm import run_tool_loop
from refinery_ai.models import RecipeRecommendation, RepairMode, VerificationResult

_SPRINGFOX_MARKERS = ("springfox.", "transformation of Docket", "Migrate manually")
_MISSING_SWAGGER_1_ANNOTATIONS = "package io.swagger.annotations does not exist"


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
    """Retain annotations required by older OpenAPI Generator Spring templates."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _MISSING_SWAGGER_1_ANNOTATIONS not in diagnostic:
        return False
    pom = repository / "pom.xml"
    if not pom.is_file():
        return False
    content = pom.read_text(errors="replace")
    if "openapi-generator-maven-plugin" not in content:
        return False
    if "<artifactId>swagger-annotations</artifactId>" in content:
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


def springdoc_repair_applicable(
    repository: Path, failure: VerificationResult
) -> bool:
    """Return whether a failed build has a known, scoped Springdoc repair."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    pom = repository / "pom.xml"
    generated_annotation_failure = (
        _MISSING_SWAGGER_1_ANNOTATIONS in diagnostic
        and pom.is_file()
        and "openapi-generator-maven-plugin" in pom.read_text(errors="replace")
        and "<artifactId>swagger-annotations</artifactId>"
        not in pom.read_text(errors="replace")
    )
    return bool(springdoc_repair_files(repository)) or generated_annotation_failure


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
        allowed = springdoc_repair_files(repository)
        if not allowed:
            if generator_repaired:
                return _result(
                    0,
                    stdout=(
                        "Preserved the Swagger annotation API required by generated DTOs after "
                        "removing the Springfox runtime"
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
