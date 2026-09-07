"""AI agent for repairing semantic migration leftovers."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider

from refinery_ai.config import Settings
from refinery_ai.migrations.deterministic_repair import springdoc_repair_files
from refinery_ai.migrations.repair import repair_result
from refinery_ai.models import RecipeRecommendation, RepairMode, VerificationResult
from refinery_ai.telemetry import record_llm_usage


class RepairFileReplacement(BaseModel):
    path: str
    content: str


class RepairEditProposal(BaseModel):
    diagnosis: str
    summary: str
    files: list[RepairFileReplacement] = Field(min_length=1)


REPAIR_SYSTEM_PROMPT = "You repair Java migrations with minimal, behavior-preserving patches."


class AiRepairAgent(Agent[None, RepairEditProposal]):
    """Pydantic AI agent for validated, scoped source replacements."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.repair_mode is RepairMode.LOCAL:
            _require_local_endpoint(settings.ollama_base_url)
        repair_model = _repair_model(
            settings.repair_model,
            ollama_base_url=settings.ollama_base_url,
            openai_api_key=(
                settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
            ),
        )
        super().__init__(
            repair_model,
            output_type=ToolOutput(
                RepairEditProposal,
                name="submit_migration_repair",
                description="Return complete replacements for the smallest safe set of files.",
                strict=False,
            ),
            retries=3,
            system_prompt=REPAIR_SYSTEM_PROMPT,
        )

    def repair(
        self,
        repository: Path,
        step: RecipeRecommendation,
        failure: VerificationResult,
    ) -> VerificationResult:
        allowed = _ai_repair_files(repository, step, failure)
        if not allowed:
            return repair_result(1, "No safely scoped repair files were found in the failure")
        context = []
        for relative in allowed:
            content = (repository / relative).read_text(errors="replace")
            context.append(f"--- {relative}\n{content[: self.settings.max_output_characters]}")
        prompt = (
            "Return complete replacement content for the smallest set of allowed files needed "
            "to make the migration build and tests pass. Address every distinct error in the "
            "current build output, including errors from multiple modules, rather than fixing "
            "only the first file. Preserve application behavior, validation semantics, API "
            "grouping, and authorization behavior. A test expecting HTTP 400 indicates request "
            "validation metadata must be preserved; inspect both generator configuration and "
            "the OpenAPI contract. Do not disable checks, remove tests, weaken validation, or "
            "edit files outside Allowed files. Do not invent dependency versions or repositories; "
            "only change dependency coordinates when the supplied files or build diagnostics "
            "provide evidence for a resolvable version.\n"
            f"Recipe: {step.recipe}\nAllowed files: {[str(path) for path in allowed]}\n"
            f"Build failure:\n{failure.model_dump_json()}\nSources:\n" + "\n".join(context)
        )
        try:
            result = self.run_sync(prompt)
            proposal = result.output
            record_llm_usage(result.usage, model=self.settings.repair_model)
        except Exception as error:
            return repair_result(1, f"Repair agent failed or returned invalid output: {error}")
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
        raise ValueError("Local repair mode requires REFINERY_OLLAMA_BASE_URL")
    host = urlparse(api_base).hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Local repair mode requires a loopback model endpoint")


def _repair_model(
    model: str,
    *,
    ollama_base_url: str | None,
    openai_api_key: str | None,
) -> str | OpenAIChatModel | OpenAIResponsesModel:
    if model.startswith("ollama:") and ollama_base_url:
        model_name = model.removeprefix("ollama:")
        base_url = ollama_base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        return OpenAIChatModel(model_name, provider=OllamaProvider(base_url=base_url))
    if model.startswith("openai:"):
        model_name = model.removeprefix("openai:")
        return (
            OpenAIResponsesModel(
                model_name,
                provider=OpenAIProvider(api_key=openai_api_key),
            )
            if openai_api_key
            else OpenAIResponsesModel(model_name, provider="openai")
        )
    return model


def _ai_repair_files(
    repository: Path,
    step: RecipeRecommendation,
    failure: VerificationResult,
) -> tuple[Path, ...]:
    """Return existing files directly implicated by a failed migration build."""

    root = repository.resolve()
    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    allowed = set(springdoc_repair_files(repository))

    for match in re.finditer(
        r"(?P<path>(?:[A-Za-z]:)?[^\s:\[\]()]+\.(?:java|kt|groovy|xml|gradle|kts|ya?ml|properties))",
        diagnostic,
    ):
        candidate = Path(match.group("path"))
        if not candidate.is_absolute():
            candidate = repository / candidate
        candidate = candidate.resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            allowed.add(candidate.relative_to(root))

    missing_symbols = set(
        re.findall(r"symbol:\s+(?:class|variable|method)\s+([A-Za-z_$][A-Za-z0-9_$]*)", diagnostic)
    )
    if missing_symbols:
        for candidate in repository.glob("**/src/**/*.java"):
            if ".refinery" in candidate.relative_to(repository).parts:
                continue
            content = candidate.read_text(errors="replace")
            if any(symbol in content for symbol in missing_symbols):
                allowed.add(candidate.relative_to(repository))

    implicated = tuple(allowed)
    for relative in implicated:
        parent = (repository / relative).parent
        while parent.is_relative_to(root):
            for build_file in ("pom.xml", "build.gradle", "build.gradle.kts"):
                candidate = parent / build_file
                if candidate.is_file():
                    allowed.add(candidate.resolve().relative_to(root))
            if parent == root:
                break
            parent = parent.parent

    for build_file in ("pom.xml", "build.gradle", "build.gradle.kts"):
        candidate = repository / build_file
        if candidate.is_file():
            allowed.add(Path(build_file))

    if step.recipe == "org.openrewrite.java.springdoc.SpringFoxToSpringDoc":
        for pattern in ("src/main/resources/*.yml", "src/main/resources/*.yaml"):
            allowed.update(path.relative_to(repository) for path in repository.glob(pattern))

    return tuple(sorted(allowed, key=str))
