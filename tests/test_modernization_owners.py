from refinery_ai.modernization.owners import maven_owners


def test_properties_boms_and_parent_ownership(tmp_path):
    (tmp_path / "pom.xml").write_text(
        """<project><parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>2.7.18</version></parent><properties><lib.version>1.2.3</lib.version></properties><dependencies><dependency><groupId>g</groupId><artifactId>a</artifactId><version>${lib.version}</version></dependency><dependency><groupId>g</groupId><artifactId>managed</artifactId></dependency></dependencies></project>"""
    )
    owners = maven_owners(tmp_path)
    assert owners[("g", "a")]["version"] == "1.2.3"
    assert owners[("g", "a")]["property"] == "lib.version"
    assert owners[("org.springframework.boot", "spring-boot-starter-parent")]["kind"] == "parent"
    assert owners[("g", "managed")]["version"] is None
