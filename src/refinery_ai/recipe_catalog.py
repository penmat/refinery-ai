"""Retrieve the public OpenRewrite Java migration recipe index."""

from __future__ import annotations

import ssl
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

JAVA_MIGRATION_CATALOG_URL = "https://docs.openrewrite.org/recipes/java/migrate"


class RecipeCatalogEntry(BaseModel):
    name: str
    url: str
    kind: str


class JavaMigrationCatalog(BaseModel):
    source: str = JAVA_MIGRATION_CATALOG_URL
    categories: list[RecipeCatalogEntry] = Field(default_factory=list)
    recipes: list[RecipeCatalogEntry] = Field(default_factory=list)

    def planning_context(self) -> str:
        """Render catalog evidence without presenting a competing JSON output shape."""

        lines = [f"Source: {self.source}", "", "Categories:"]
        lines.extend(f"- {entry.name} ({entry.url})" for entry in self.categories)
        lines.extend(["", "Recipes:"])
        lines.extend(
            f"- [{entry.kind}] {entry.name} ({entry.url})" for entry in self.recipes
        )
        return "\n".join(lines)


class _CatalogParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.source = source
        self.in_main = False
        self.heading: str | None = None
        self.capture_heading = False
        self.heading_text: list[str] = []
        self.link: str | None = None
        self.link_text: list[str] = []
        self.entries: list[RecipeCatalogEntry] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "main":
            self.in_main = True
        elif self.in_main and tag == "h2":
            self.capture_heading = True
            self.heading_text = []
        elif self.in_main and self.heading in {"Categories", "Composite Recipes", "Recipes"}:
            href = values.get("href") if tag == "a" else None
            if href and href.startswith("/recipes/java/migrate/"):
                self.link = urljoin(self.source, href)
                self.link_text = []

    def handle_data(self, data: str) -> None:
        if self.capture_heading:
            self.heading_text.append(data)
        if self.link is not None:
            self.link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self.in_main = False
        elif tag == "h2" and self.capture_heading:
            self.heading = " ".join("".join(self.heading_text).split()).removesuffix("​")
            self.capture_heading = False
        elif tag == "a" and self.link is not None:
            name = " ".join("".join(self.link_text).split())
            if name:
                self.entries.append(
                    RecipeCatalogEntry(name=name, url=self.link, kind=self.heading or "Recipes")
                )
            self.link = None


def parse_java_migration_catalog(html: str) -> JavaMigrationCatalog:
    parser = _CatalogParser(JAVA_MIGRATION_CATALOG_URL)
    parser.feed(html)
    unique = {(entry.kind, entry.url): entry for entry in parser.entries}
    entries = list(unique.values())
    return JavaMigrationCatalog(
        categories=[entry for entry in entries if entry.kind == "Categories"],
        recipes=[entry for entry in entries if entry.kind != "Categories"],
    )


def fetch_java_migration_catalog(
    timeout: float = 15, *, verify_ssl: bool = True
) -> JavaMigrationCatalog:
    """Fetch and parse the canonical catalog page without executing any recipe."""

    request = Request(JAVA_MIGRATION_CATALOG_URL, headers={"User-Agent": "refinery-ai/0.1"})
    with urlopen(request, timeout=timeout, context=_ssl_context(verify_ssl)) as response:
        final_url = response.geturl()
        if urlparse(final_url).netloc != "docs.openrewrite.org":
            raise ValueError(f"Unexpected recipe catalog redirect: {final_url}")
        catalog = parse_java_migration_catalog(response.read().decode("utf-8"))
    if not catalog.recipes:
        raise ValueError("OpenRewrite Java migration catalog contained no recipes")
    return catalog


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
