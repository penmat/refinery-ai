"""Download and normalize declarative OpenRewrite recipe definitions."""

from __future__ import annotations

import io
import json
import ssl
import time
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

RECIPE_SOURCE_PREFIX = "src/main/resources/META-INF/rewrite/"
RECIPE_REPOSITORIES = (
    "openrewrite/rewrite-migrate-java",
    "openrewrite/rewrite-spring",
)
TRANSIENT_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
RECIPE_CACHE_SCHEMA_VERSION = 2


def _ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    if verify_ssl:
        try:
            import certifi
        except ImportError:
            return ssl.create_default_context()
        return ssl.create_default_context(cafile=certifi.where())
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class RecipeSource(BaseModel):
    recipe: str
    display_name: str
    description: str
    repository: str
    file_path: str
    recipe_list: list[str | dict[str, Any]] = Field(default_factory=list)


def parse_recipe_yaml(
    content: str, file_path: str, *, repository: str = "openrewrite/rewrite-migrate-java"
) -> list[RecipeSource]:
    """Extract recipe documents from one OpenRewrite YAML resource."""

    recipes: list[RecipeSource] = []
    for document in yaml.safe_load_all(content):
        if not isinstance(document, dict):
            continue
        if document.get("type") != "specs.openrewrite.org/v1beta/recipe":
            continue
        name = document.get("name")
        description = document.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            continue
        display_name = document.get("displayName")
        recipe_list = document.get("recipeList")
        recipes.append(
            RecipeSource(
                recipe=name,
                display_name=display_name if isinstance(display_name, str) else name,
                description=" ".join(description.split()),
                repository=repository,
                file_path=file_path,
                recipe_list=(
                    recipe_list
                    if isinstance(recipe_list, list)
                    and all(isinstance(item, (str, dict)) for item in recipe_list)
                    else []
                ),
            )
        )
    return recipes


def _fetch_repository_recipes(
    repository: str, timeout: float, verify_ssl: bool
) -> list[RecipeSource]:
    """Download one trusted GitHub repository and extract its declarative recipes."""

    archive_url = f"https://github.com/{repository}/archive/refs/heads/main.zip"
    request = Request(archive_url, headers={"User-Agent": "refinery-ai/0.1"})
    context: ssl.SSLContext = _ssl_context(verify_ssl)
    with urlopen(request, timeout=timeout, context=context) as response:
        final_host = urlparse(response.geturl()).hostname
        if final_host not in {"github.com", "codeload.github.com"}:
            raise ValueError(f"Unexpected recipe archive redirect: {response.geturl()}")
        archive = response.read()

    recipes: list[RecipeSource] = []
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for member in bundle.infolist():
            path = PurePosixPath(member.filename)
            relative = "/".join(path.parts[1:])
            if not relative.startswith(RECIPE_SOURCE_PREFIX):
                continue
            if path.suffix.lower() not in {".yml", ".yaml"}:
                continue
            content = bundle.read(member).decode("utf-8")
            recipes.extend(parse_recipe_yaml(content, relative, repository=repository))
    if not recipes:
        raise ValueError(f"{repository} archive contained no declarative recipes")
    return recipes


def _cache_path(cache_directory: Path, repository: str) -> Path:
    """Return a stable cache path for one trusted recipe repository."""

    return cache_directory / f"{repository.replace('/', '--')}.json"


def _read_cached_recipes(cache_directory: Path, repository: str) -> list[RecipeSource] | None:
    """Read a valid repository cache, ignoring absent or corrupt cache entries."""

    path = _cache_path(cache_directory, repository)
    try:
        payload = json.loads(path.read_text())
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != RECIPE_CACHE_SCHEMA_VERSION
            or payload.get("repository") != repository
        ):
            return None
        recipes = [RecipeSource.model_validate(item) for item in payload["recipes"]]
    except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not recipes or any(recipe.repository != repository for recipe in recipes):
        return None
    return recipes


def _write_cached_recipes(
    cache_directory: Path, repository: str, recipes: list[RecipeSource]
) -> None:
    """Atomically persist one repository's normalized recipe definitions."""

    cache_directory.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_directory, repository)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": RECIPE_CACHE_SCHEMA_VERSION,
                "repository": repository,
                "recipes": [recipe.model_dump() for recipe in recipes],
            },
            indent=2,
        )
    )
    temporary.replace(path)


def _is_transient_failure(error: BaseException) -> bool:
    if isinstance(error, HTTPError):
        return error.code in TRANSIENT_HTTP_STATUS_CODES
    return isinstance(error, (URLError, TimeoutError))


def fetch_recipe_sources(
    timeout: float = 30,
    *,
    verify_ssl: bool = True,
    cache_directory: Path | None = None,
    retry_attempts: int = 3,
    retry_delay_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    refresh: bool = False,
) -> list[RecipeSource]:
    """Load cached catalogs, downloading only missing entries or when refresh is requested."""

    if retry_attempts < 1:
        raise ValueError("retry_attempts must be at least 1")

    recipes: list[RecipeSource] = []
    for repository in RECIPE_REPOSITORIES:
        cached = (
            _read_cached_recipes(cache_directory, repository)
            if cache_directory is not None
            else None
        )
        if cached is not None and not refresh:
            recipes.extend(cached)
            continue

        failure: BaseException | None = None
        repository_recipes: list[RecipeSource] | None = None
        for attempt in range(retry_attempts):
            try:
                repository_recipes = _fetch_repository_recipes(
                    repository, timeout, verify_ssl
                )
                failure = None
                break
            except (HTTPError, URLError, TimeoutError) as error:
                failure = error
                if not _is_transient_failure(error) or attempt == retry_attempts - 1:
                    break
                sleep(retry_delay_seconds * (2**attempt))

        if repository_recipes is None:
            repository_recipes = cached
        if repository_recipes is None:
            assert failure is not None
            raise failure
        if cache_directory is not None and failure is None:
            _write_cached_recipes(cache_directory, repository, repository_recipes)
        recipes.extend(repository_recipes)
    return recipes
