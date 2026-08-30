from pathlib import Path
from types import SimpleNamespace

import pytest

from refinery_ai.config import Settings
from refinery_ai.migrations.ai_repair import (
    AiMigrationRepairer,
    configure_boot3_test_compatibility,
    configure_openapi_generator_for_springdoc,
    springdoc_repair_applicable,
    springdoc_repair_files,
)
from refinery_ai.models import RecipeRecommendation, RepairMode, VerificationResult


def test_local_repair_requires_loopback_endpoint() -> None:
    with pytest.raises(ValueError, match="loopback"):
        AiMigrationRepairer(
            Settings(
                repair_mode=RepairMode.LOCAL,
                litellm_api_base="https://models.example.com/v1",
            )
        )


def test_springdoc_repair_scope_contains_only_residual_java(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/SwaggerConfiguration.java"
    source.parent.mkdir(parents=True)
    source.write_text("import springfox.documentation.spring.web.plugins.Docket;\n")
    other = tmp_path / "src/main/java/Controller.java"
    other.write_text("class Controller {}\n")

    assert springdoc_repair_files(tmp_path) == (
        Path("src/main/java/SwaggerConfiguration.java"),
    )


def test_ai_repair_uses_scoped_full_file_replacement(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "src/main/java/SwaggerConfiguration.java"
    source.parent.mkdir(parents=True)
    source.write_text("import springfox.documentation.spring.web.plugins.Docket;\n")
    response = SimpleNamespace(
        content=(
            '{"diagnosis":"Springfox remains","summary":"Converted Docket",'
            '"files":[{"path":"src/main/java/SwaggerConfiguration.java",'
            '"content":"class SwaggerConfiguration {}\\n"}]}'
        )
    )
    monkeypatch.setattr(
        "refinery_ai.migrations.ai_repair.run_tool_loop", lambda *args, **kwargs: response
    )
    repairer = AiMigrationRepairer(
        Settings(repair_mode=RepairMode.CONFIGURED_PROVIDER)
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
        reason="Upgrade Boot.",
        evidence=["boot"],
        expected_benefit="Boot 2.7.",
        priority="high",
        confidence=1,
    )

    result = repairer.repair(
        tmp_path,
        step,
        VerificationResult(command=["mvn", "test"], exit_code=1, duration_seconds=0),
    )

    assert result.green
    assert source.read_text() == "class SwaggerConfiguration {}\n"


def test_springdoc_repair_configures_generated_openapi_annotations(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <dependencies>
  </dependencies>
  <plugin>
    <artifactId>openapi-generator-maven-plugin</artifactId>
    <configuration>
      <configOptions>
        <dateLibrary>java8</dateLibrary>
      </configOptions>
    </configuration>
  </plugin>
</project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout="package io.swagger.annotations does not exist",
        duration_seconds=0,
    )

    assert configure_openapi_generator_for_springdoc(tmp_path, failure)
    assert "<groupId>io.swagger</groupId>" in pom.read_text()
    assert "<artifactId>swagger-annotations</artifactId>" in pom.read_text()
    assert not springdoc_repair_applicable(tmp_path, failure)


def test_springdoc_repair_preserves_v1_annotations_alongside_v3(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <dependencies>
    <dependency>
      <groupId>io.swagger.core.v3</groupId>
      <artifactId>swagger-annotations</artifactId>
      <version>2.2.54</version>
    </dependency>
  </dependencies>
  <artifactId>openapi-generator-maven-plugin</artifactId>
</project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout="package io.swagger.annotations does not exist",
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert configure_openapi_generator_for_springdoc(tmp_path, failure)
    updated = pom.read_text()
    assert "<groupId>io.swagger.core.v3</groupId>" in updated
    assert "<groupId>io.swagger</groupId>" in updated


def test_boot3_repair_upgrades_openapi_generator_to_jakarta_mode(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <properties>
    <openapi-generator-maven-plugin.version>5.2.1</openapi-generator-maven-plugin.version>
  </properties>
  <artifactId>openapi-generator-maven-plugin</artifactId>
  <configOptions>
    <performBeanValidation>true</performBeanValidation>
  </configOptions>
</project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout=(
            "target/generated-sources/openapi/Dto.java: "
            "package javax.validation does not exist"
        ),
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert configure_openapi_generator_for_springdoc(tmp_path, failure)
    updated = pom.read_text()
    assert "<openapi-generator-maven-plugin.version>6.6.0" in updated
    assert "<useSpringBoot3>true</useSpringBoot3>" in updated


def test_ai_repair_can_fix_generated_annotations_without_residual_java(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <dependencies>
  </dependencies>
  <artifactId>openapi-generator-maven-plugin</artifactId>
  <configOptions>
  </configOptions>
</project>
"""
    )
    repairer = AiMigrationRepairer(Settings(repair_mode=RepairMode.CONFIGURED_PROVIDER))
    result = repairer.repair(
        tmp_path,
        RecipeRecommendation(
            recipe="org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
            reason="Replace Springfox.",
            evidence=["springfox"],
            expected_benefit="Use Springdoc.",
            priority="high",
            confidence=1,
        ),
        VerificationResult(
            command=["./mvnw", "test"],
            exit_code=1,
            stdout="package io.swagger.annotations does not exist",
            duration_seconds=0,
        ),
    )

    assert result.green
    assert "OpenAPI Generator compatibility" in result.stdout


def test_boot3_repair_preserves_generated_validation_and_updates_legacy_jpql(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <artifactId>openapi-generator-maven-plugin</artifactId>
  <configOptions>
    <useSpringBoot3>true</useSpringBoot3>
  </configOptions>
</project>
"""
    )
    repository = tmp_path / "src/main/java/example/jpa/PetRepositoryImpl.java"
    repository.parent.mkdir(parents=True)
    repository.write_text(
        'query("DELETE FROM Visit visit WHERE pet_id=" + id);\n'
        'query("SELECT pet FROM Pet pet WHERE type_id=" + id);\n'
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout=(
            "Status expected:<400> but was:<201>\n"
            "shouldDeletePet:213 » InvalidDataAccessApiUsage"
        ),
        duration_seconds=0,
    )

    repairs = configure_boot3_test_compatibility(tmp_path, failure)

    assert repairs
    assert "<useBeanValidation>true</useBeanValidation>" in pom.read_text()
    updated = repository.read_text()
    assert "WHERE visit.pet.id=" in updated
    assert "WHERE pet.type.id=" in updated
    assert springdoc_repair_applicable(tmp_path, failure)


def test_boot3_repair_requires_nonempty_openapi_request_fields(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        "<project><artifactId>openapi-generator-maven-plugin</artifactId>"
        "<useBeanValidation>true</useBeanValidation></project>"
    )
    spec = tmp_path / "src/main/resources/api-docs.yml"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        """components:
  schemas:
    PetType:
      type: object
      properties:
        id:
          type: integer
          readOnly: true
        name:
          type: string
          minLength: 1
      required:
        - id
    User:
      type: object
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout=(
            "PetTypeRestControllerTests.testCreatePetTypeError "
            "Status expected:<400> but was:<201>"
        ),
        duration_seconds=0,
    )

    repairs = configure_boot3_test_compatibility(tmp_path, failure)

    assert repairs
    assert "      required:\n        - id\n        - name\n" in spec.read_text()


def test_boot3_repair_does_not_rewrite_jdbc_sql_and_can_restore_old_damage(
    tmp_path: Path,
) -> None:
    jdbc = tmp_path / "src/main/java/example/jdbc/JdbcVisitRepositoryImpl.java"
    jdbc.parent.mkdir(parents=True)
    jdbc.write_text('query("SELECT * FROM visits WHERE pet_id=:id");\n')
    jpa = tmp_path / "src/main/java/example/jpa/JpaVisitRepositoryImpl.java"
    jpa.parent.mkdir(parents=True)
    jpa.write_text('query("DELETE FROM Visit visit WHERE pet_id=:id");\n')

    configure_boot3_test_compatibility(
        tmp_path,
        VerificationResult(
            command=["test"],
            exit_code=1,
            stdout="InvalidDataAccessApiUsage",
            duration_seconds=0,
        ),
    )

    assert "WHERE pet_id=" in jdbc.read_text()
    assert "WHERE visit.pet.id=" in jpa.read_text()
    jdbc.write_text('query("SELECT * FROM visits WHERE visit.pet.id=:id");\n')

    configure_boot3_test_compatibility(
        tmp_path,
        VerificationResult(
            command=["test"],
            exit_code=1,
            stdout="BadSqlGrammar",
            duration_seconds=0,
        ),
    )

    assert "WHERE pet_id=" in jdbc.read_text()
