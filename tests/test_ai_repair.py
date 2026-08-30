from pathlib import Path
from types import SimpleNamespace

import pytest

from refinery_ai.config import Settings
from refinery_ai.migrations.ai_repair import (
    AiMigrationRepairer,
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
    assert "Swagger annotation API" in result.stdout
