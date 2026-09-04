"""LLM-backed repair for semantic migration leftovers."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, TypeAdapter

from refinery_ai.config import Settings
from refinery_ai.llm import run_tool_loop
from refinery_ai.migrations.deterministic_repair import springdoc_repair_files
from refinery_ai.migrations.repair import repair_result
from refinery_ai.models import RecipeRecommendation, RepairMode, VerificationResult


class RepairFileReplacement(BaseModel):
    path: str
    content: str


class RepairEditProposal(BaseModel):
    diagnosis: str
    summary: str
    files: list[RepairFileReplacement] = Field(min_length=1)


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
        allowed = springdoc_repair_files(repository)
        if not allowed:
            return repair_result(1, "No residual Springfox source files were found")
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
            "the Springfox Docket to Springdoc conversion. Preserve API grouping and "
            "authorization behavior. Do not disable checks or edit files outside Allowed "
            "files.\n"
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
            return repair_result(1, f"Repair model failed or returned invalid JSON: {error}")
        allowed_names = {str(path) for path in allowed}
        changed = [replacement.path for replacement in proposal.files]
        if len(changed) != len(set(changed)) or set(changed) - allowed_names:
            return repair_result(1, "Repair proposal attempted files outside the allowed scope")
        for replacement in proposal.files:
            target = (repository / replacement.path).resolve()
            if not target.is_relative_to(repository.resolve()) or not target.is_file():
                return repair_result(1, f"Unsafe or missing repair target: {replacement.path}")
        for replacement in proposal.files:
            (repository / replacement.path).write_text(replacement.content)
        return repair_result(0, stdout=proposal.summary)


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
