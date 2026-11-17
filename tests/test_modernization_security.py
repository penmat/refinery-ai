from refinery_ai.inspectors.dependencies.collectors import ResolvedDependencyCollector
from refinery_ai.inspectors.dependencies.osv import OsvClient


def test_osv_preserves_remediation_evidence(monkeypatch):
    payload = {
        "id": "GHSA-test",
        "aliases": ["CVE-test"],
        "references": [{"type": "ADVISORY", "url": "https://example.com/advisory"}],
        "affected": [
            {
                "package": {"ecosystem": "Maven", "name": "g:a"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.0"}]}
                ],
            }
        ],
    }
    client = OsvClient("https://api.osv.dev", 10)
    monkeypatch.setattr(client, "_request_json", lambda *args: payload)
    result = client._vulnerability("GHSA-test")
    assert result.affected == payload["affected"]
    assert result.aliases == ["CVE-test"]
    assert result.references == payload["references"]
    assert result.retrieved_at


def test_security_inventory_tracks_transitive_owners():
    collector = ResolvedDependencyCollector("3.8.1", 10, 1000)
    tree = {
        "groupId": "app",
        "artifactId": "root",
        "version": "1",
        "children": [
            {
                "groupId": "g",
                "artifactId": "owner",
                "version": "1",
                "children": [{"groupId": "g", "artifactId": "child", "version": "1"}],
            }
        ],
    }
    deps = []
    collector._walk_maven(tree, deps, depth=0)
    assert deps[1].dependency_path == ("g:owner:1", "g:child:1")
    assert deps[1].module == "app:root"


def test_candidate_security_checks(tmp_path):
    from refinery_ai.inspectors.dependencies.models import Vulnerability
    from refinery_ai.modernization.security import remediation_candidates
    from refinery_ai.modernization.versions import VersionCandidateProvider

    versions = VersionCandidateProvider(
        tmp_path,
        fetch=lambda url: (
            "<metadata><versioning><versions><version>1.0.1</version><version>"
            "1.0.2</version></versions></versioning></metadata>"
        ),
    )

    class Scanner:
        def query(self, purls):
            return {
                p: ([Vulnerability("other", None, None, "high")] if p.endswith("1.0.1") else [])
                for p in purls
            }

    result = remediation_candidates("g", "a", "1.0.0", versions, Scanner())
    assert [r.version for r in result] == ["1.0.2"]


def test_bom_candidate_versions_resolve_properties_and_imports(tmp_path):
    from refinery_ai.modernization.versions import VersionCandidateProvider

    def fetch(url):
        if "/child/" in url:
            return (
                "<project><dependencyManagement><dependencies><dependency>"
                "<groupId>g</groupId><artifactId>lib</artifactId><version>"
                "2.0.1</version></dependency></dependencies>"
                "</dependencyManagement></project>"
            )
        return (
            "<project><properties><child.version>2.0</child.version>"
            "</properties><dependencyManagement><dependencies><dependency>"
            "<groupId>g</groupId><artifactId>child</artifactId><version>"
            "${child.version}</version><scope>import</scope><type>pom</type>"
            "</dependency></dependencies></dependencyManagement></project>"
        )

    provider = VersionCandidateProvider(tmp_path, fetch=fetch)
    assert provider.managed_versions("g", "root", "2.0")[("g", "lib")] == "2.0.1"


def test_malformed_osv_result_is_not_clean(monkeypatch):
    import pytest

    from refinery_ai.inspectors.dependencies.models import DependencyAnalysisError

    client = OsvClient("https://api.osv.dev", 10)
    for entry in (None, {"vulns": None}, {"vulns": [{}]}, {"error": "unavailable"}):
        monkeypatch.setattr(
            client, "_request_json", lambda *args, entry=entry: {"results": [entry]}
        )
        with pytest.raises(DependencyAnalysisError, match="unexpected"):
            client.query(["pkg:maven/g/a@1"])


def test_security_considers_newest_patch_and_major_fix(tmp_path):
    from refinery_ai.inspectors.dependencies.models import Vulnerability
    from refinery_ai.modernization.security import remediation_candidates
    from refinery_ai.modernization.versions import VersionCandidateProvider

    provider = VersionCandidateProvider(
        tmp_path,
        fetch=lambda url: (
            "<metadata><versioning><versions>"
            + "".join(f"<version>1.0.{i}</version>" for i in range(1, 22))
            + "<version>2.0.0</version></versions></versioning></metadata>"
        ),
    )

    class Scanner:
        def query(self, purls):
            return {
                p: []
                if p.endswith(("1.0.21", "2.0.0"))
                else [Vulnerability("CVE", None, None, "high")]
                for p in purls
            }

    result = remediation_candidates("g", "a", "1.0.0", provider, Scanner())
    assert [v.version for v in result] == ["1.0.21", "2.0.0"]


def test_transitive_fix_upgrades_declared_bom_owner(tmp_path, monkeypatch):
    from test_modernization_resolver import Planner, evidence

    from refinery_ai.config import Settings
    from refinery_ai.modernization.resolver import resolve_intent
    from refinery_ai.modernization.versions import VersionCandidate, VersionCandidateProvider
    from refinery_ai.recipes.catalog import RecipeCatalogSession
    from refinery_ai.recipes.sources import RecipeSource

    observed = evidence(tmp_path)
    observed.owners = {"g:platform": {"kind": "bom", "version": "1.0.0"}}
    observed.inventory = [{"group": "g", "artifact": "lib", "version": "1.0.0", "direct": False}]
    observed.vulnerabilities = {"pkg:maven/g/lib@1.0.0": [{"id": "CVE-test"}]}
    monkeypatch.setattr(
        VersionCandidateProvider,
        "candidates",
        lambda *args: [
            VersionCandidate(
                version="1.0.1", source="https://repo.test/metadata", retrieved_at="2026-11-17"
            )
        ],
    )
    monkeypatch.setattr(
        VersionCandidateProvider,
        "managed_versions",
        lambda self, g, a, v, **kwargs: {("g", "lib"): v},
    )
    monkeypatch.setattr(OsvClient, "query", lambda self, purls: {p: [] for p in purls})
    catalog = RecipeCatalogSession(
        [
            RecipeSource(
                recipe="org.openrewrite.maven.UpgradeDependencyVersion",
                display_name="Upgrade",
                description="Upgrade",
                repository="openrewrite/rewrite",
                file_path="descriptor",
                artifact="org.openrewrite:rewrite-maven:8.90.2",
            )
        ]
    )
    plan = resolve_intent(
        tmp_path,
        17,
        "recommended",
        Settings(),
        planner=Planner(),
        evidence=observed,
        catalog=catalog,
    )
    objective = next(o for o in plan.objectives if o.category == "security")
    assert objective.disposition == "executable"
    assert objective.target.artifact == "platform"
    assert objective.transformations[0].options == {
        "groupId": "g",
        "artifactId": "platform",
        "newVersion": "1.0.1",
    }
