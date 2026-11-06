"""AI agent for repairing semantic migration leftovers."""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr

from refinery_ai.build_tools import maven_command_prefix
from refinery_ai.config import Settings
from refinery_ai.migrations.deterministic_repair import springdoc_repair_files
from refinery_ai.migrations.process_safety import ownership_active, run_owned
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


class AiRepairAgent:
    """LangGraph agent for validated, scoped source replacements."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._proposal_fingerprints: set[str] = set()
        self._last_replacements: dict[Path, str] = {}
        self._dependency_feedback: list[str] = []
        if settings.repair_mode is RepairMode.LOCAL:
            _require_local_endpoint(settings.ollama_base_url)
        self.model = _repair_model(
            settings.repair_model,
            ollama_base_url=settings.ollama_base_url,
            openai_api_key=(
                settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
            ),
        )
        self.agent = create_agent(
            self.model,
            tools=[],
            system_prompt=REPAIR_SYSTEM_PROMPT,
            response_format=ToolStrategy(RepairEditProposal),
            name="refinery-repair",
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
            rendered = _repair_file_context(
                relative,
                content,
                failure,
                self.settings.max_output_characters,
            )
            context.append(f"--- {relative}\n{rendered}")
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
            f"Build failure:\n{failure.model_dump_json()}\n"
            f"Rejected dependency proposals: {self._dependency_feedback}\nSources:\n"
            + "\n".join(context)
        )
        try:
            result = self.agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={"metadata": {"refinery.phase": "repair"}},
            )
            proposal = RepairEditProposal.model_validate(result["structured_response"])
            record_llm_usage(
                _usage_from_messages(result.get("messages")),
                model=self.settings.repair_model,
            )
        except Exception as error:
            return repair_result(1, f"Repair agent failed or returned invalid output: {error}")
        allowed_names = {str(path) for path in allowed}
        changed = [replacement.path for replacement in proposal.files]
        if len(changed) != len(set(changed)) or set(changed) - allowed_names:
            return repair_result(1, "Repair proposal attempted files outside the allowed scope")
        fingerprint = sha256(
            "\0".join(
                f"{replacement.path}\0{replacement.content}"
                for replacement in sorted(proposal.files, key=lambda item: item.path)
            ).encode()
        ).hexdigest()
        if fingerprint in self._proposal_fingerprints:
            return repair_result(1, "Repair agent repeated an earlier proposal")
        self._proposal_fingerprints.add(fingerprint)
        for replacement in proposal.files:
            target = (repository / replacement.path).resolve()
            if not target.is_relative_to(repository.resolve()) or not target.is_file():
                return repair_result(1, f"Unsafe or missing repair target: {replacement.path}")
        if all(
            (repository / replacement.path).read_text(errors="replace") == replacement.content
            for replacement in proposal.files
        ):
            return repair_result(1, "Repair proposal did not change any files")
        self._last_replacements = {
            repository / replacement.path: (repository / replacement.path).read_text(
                errors="replace"
            )
            for replacement in proposal.files
        }
        safety_error = _validate_repair_safety(
            self._last_replacements,
            {
                repository / replacement.path: replacement.content
                for replacement in proposal.files
            },
        )
        if safety_error:
            self._last_replacements.clear()
            return repair_result(1, safety_error)
        for replacement in proposal.files:
            (repository / replacement.path).write_text(replacement.content)
        dependency_error = _validate_new_maven_coordinates(
            repository,
            self._last_replacements,
            {
                repository / replacement.path: replacement.content
                for replacement in proposal.files
            },
        )
        if dependency_error:
            self.rollback_last()
            self._dependency_feedback.append(dependency_error)
            return repair_result(1, dependency_error)
        return repair_result(0, stdout=proposal.summary)

    def accept_last(self) -> None:
        """Keep the most recent proposal after verification shows improvement."""

        self._last_replacements.clear()

    def rollback_last(self) -> None:
        """Restore files changed by the most recent unsuccessful proposal."""

        for path, content in self._last_replacements.items():
            path.write_text(content)
        self._last_replacements.clear()


def _require_local_endpoint(api_base: str | None) -> None:
    if not api_base:
        raise ValueError("Local repair mode requires REFINERY_OLLAMA_BASE_URL")
    host = urlparse(api_base).hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Local repair mode requires a loopback model endpoint")


def _maven_dependencies(content: str) -> set[tuple[str, str, str]]:
    """Extract literal dependency coordinates from one Maven project file."""

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return set()

    def child_text(element: ET.Element, name: str) -> str | None:
        for child in element:
            if child.tag.rsplit("}", 1)[-1] == name and child.text:
                return child.text.strip()
        return None

    coordinates: set[tuple[str, str, str]] = set()
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "dependency":
            continue
        group = child_text(element, "groupId")
        artifact = child_text(element, "artifactId")
        version = child_text(element, "version")
        if group and artifact and version and "${" not in version:
            coordinates.add((group, artifact, version))
    return coordinates


def _validate_new_maven_coordinates(
    repository: Path,
    originals: dict[Path, str],
    proposals: dict[Path, str],
) -> str | None:
    """Reject literal Maven coordinates introduced by an AI proposal if unavailable."""

    original_coordinates: set[tuple[str, str, str]] = set()
    proposed_coordinates: set[tuple[str, str, str]] = set()
    for path, content in originals.items():
        if path.name == "pom.xml":
            original_coordinates.update(_maven_dependencies(content))
    for path, content in proposals.items():
        if path.name == "pom.xml":
            proposed_coordinates.update(_maven_dependencies(content))

    for group, artifact, version in sorted(proposed_coordinates - original_coordinates):
        coordinate = f"{group}:{artifact}:{version}"
        command = [
            *maven_command_prefix(repository),
            "-q",
            "dependency:get",
            f"-Dartifact={coordinate}",
            "-Dtransitive=false",
        ]
        try:
            if ownership_active():
                result = run_owned(command, cwd=repository, timeout=120)
            else:
                result = subprocess.run(
                    command,
                    cwd=repository,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=120,
                )
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"Could not validate proposed Maven dependency {coordinate}: {error}"
        if result.returncode != 0:
            diagnostic = (result.stderr.strip() or result.stdout.strip())[-1000:]
            return (
                f"Rejected proposed Maven dependency {coordinate}: it did not resolve from "
                f"the configured repositories. {diagnostic}"
            )
    return None


def _validate_repair_safety(
    originals: dict[Path, str], proposals: dict[Path, str]
) -> str | None:
    """Reject build hooks that mutate checked-in source during verification."""

    for path, proposed in proposals.items():
        if path.name != "pom.xml" or proposed == originals.get(path, ""):
            continue
        if "maven-antrun-plugin" in proposed and any(
            marker in proposed for marker in ("src/main", "src/test", "<replace", "<replaceregexp")
        ):
            return (
                "Rejected repair that makes the Maven build mutate checked-in source files; "
                "repair the source or configuration directly instead"
            )
    return None


def _repair_model(
    model: str,
    *,
    ollama_base_url: str | None,
    openai_api_key: str | None,
) -> BaseChatModel:
    if model.startswith("ollama:") and ollama_base_url:
        model_name = model.removeprefix("ollama:")
        base_url = ollama_base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        return ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=SecretStr("ollama"),
            max_retries=3,
        )
    if model.startswith("openai:"):
        model_name = model.removeprefix("openai:")
        parameters: dict[str, Any] = {
            "model": model_name,
            "max_retries": 3,
            "use_responses_api": True,
        }
        if openai_api_key:
            parameters["api_key"] = SecretStr(openai_api_key)
        return ChatOpenAI(**parameters)
    return ChatOpenAI(model=model, max_retries=3)


def _usage_from_messages(messages: object) -> dict[str, int]:
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    if not isinstance(messages, list):
        return usage
    for message in messages:
        if not isinstance(message, AIMessage) or not message.usage_metadata:
            continue
        usage["input_tokens"] += int(message.usage_metadata.get("input_tokens", 0))
        usage["output_tokens"] += int(message.usage_metadata.get("output_tokens", 0))
        usage["requests"] += 1
    return usage


def _repair_file_context(
    relative: Path,
    content: str,
    failure: VerificationResult,
    limit: int,
) -> str:
    """Keep a relevant OpenAPI contract complete so full-file replacement remains safe."""

    if len(content) <= limit or relative.suffix.lower() not in {".yaml", ".yml"}:
        return content[:limit]
    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    schema_names = sorted(
        set(re.findall(r"([A-Z][A-Za-z0-9]+)RestControllerTests", diagnostic))
    )
    has_relevant_schema = any(
        re.search(rf"(?m)^    {re.escape(schema)}:\s*$", content) is not None
        for schema in schema_names
    )
    # Repair output uses complete file replacements. Supplying a stitched or truncated contract
    # would make a model-authored replacement destructive, so include the complete relevant file.
    return content if has_relevant_schema else content[:limit]


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
