"""Download and normalize declarative OpenRewrite recipe definitions."""

from __future__ import annotations

import io
import ssl
import zipfile
from pathlib import PurePosixPath
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

from refinery_ai.recipe_catalog import _ssl_context

RECIPE_SOURCE_PREFIX = "src/main/resources/META-INF/rewrite/"
RECIPE_REPOSITORIES = (
    "openrewrite/rewrite-migrate-java",
    "openrewrite/rewrite-spring",
)


class RecipeSource(BaseModel):
    recipe: str
    display_name: str
    description: str
    repository: str
    file_path: str


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
        recipes.append(
            RecipeSource(
                recipe=name,
                display_name=display_name if isinstance(display_name, str) else name,
                description=" ".join(description.split()),
                repository=repository,
                file_path=file_path,
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


def fetch_recipe_sources(
    timeout: float = 30, *, verify_ssl: bool = True
) -> list[RecipeSource]:
    """Download Java migration and Spring declarative recipe catalogs from GitHub."""

    recipes: list[RecipeSource] = []
    for repository in RECIPE_REPOSITORIES:
        recipes.extend(_fetch_repository_recipes(repository, timeout, verify_ssl))
    return recipes
