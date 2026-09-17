from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from refinery_ai.agents.ai_repair import AiRepairAgent, _repair_file_context
from refinery_ai.config import Settings
from refinery_ai.migrations.ai_repair import AiMigrationRepairer, _ai_repair_files
from refinery_ai.migrations.deterministic_repair import (
    DeterministicMigrationRepairer,
    configure_boot3_test_compatibility,
    configure_boot_validation_starter,
    configure_openapi_generator_for_springdoc,
    make_password_encoder_bean_independent,
    migrate_removed_hibernate6_apis,
    remove_legacy_global_method_security_inheritance,
    remove_legacy_hibernate4_integrations,
    remove_obsolete_junit_platform_surefire_provider,
    repair_springdoc_info_constructor,
    springdoc_repair_applicable,
    springdoc_repair_files,
    use_explicit_ant_path_request_matchers,
)
from refinery_ai.migrations.repair import MigrationRepairChain, MigrationRepairer
from refinery_ai.models import RecipeRecommendation, RepairMode, VerificationResult


def test_local_repair_requires_loopback_endpoint() -> None:
    with pytest.raises(ValueError, match="loopback"):
        AiMigrationRepairer(
            Settings(
                repair_mode=RepairMode.LOCAL,
                ollama_base_url="https://models.example.com/v1",
            )
        )


def test_repair_chain_uses_ai_after_deterministic_rules_do_not_match(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/SwaggerConfiguration.java"
    source.parent.mkdir(parents=True)
    source.write_text("import springfox.documentation.spring.web.plugins.Docket;\n")
    model = TestModel(
        custom_output_args={
            "diagnosis": "Springfox remains",
            "summary": "Converted Docket",
            "files": [
                {
                    "path": "src/main/java/SwaggerConfiguration.java",
                    "content": "class SwaggerConfiguration {}\n",
                }
            ],
        }
    )
    ai_repair = AiRepairAgent(Settings(repair_mode=RepairMode.CONFIGURED_PROVIDER))
    repairer = MigrationRepairChain(
        DeterministicMigrationRepairer(),
        ai_repair,
    )

    with ai_repair.override(model=model):
        result = repairer.repair(
            tmp_path,
            RecipeRecommendation(
                recipe="org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
                reason="convert",
                evidence=["springfox"],
                expected_benefit="springdoc",
                priority="high",
                confidence=1,
            ),
            VerificationResult(command=["mvn", "test"], exit_code=1, duration_seconds=0),
        )

    assert result.green
    assert result.stdout == "Converted Docket"


def test_springdoc_repair_scope_contains_only_residual_java(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/SwaggerConfiguration.java"
    source.parent.mkdir(parents=True)
    source.write_text("import springfox.documentation.spring.web.plugins.Docket;\n")
    other = tmp_path / "src/main/java/Controller.java"
    other.write_text("class Controller {}\n")

    assert springdoc_repair_files(tmp_path) == (Path("src/main/java/SwaggerConfiguration.java"),)


def test_ai_repair_satisfies_framework_neutral_contract() -> None:
    assert isinstance(
        AiRepairAgent(Settings(repair_mode=RepairMode.CONFIGURED_PROVIDER)), MigrationRepairer
    )


def test_ai_repair_uses_scoped_full_file_replacement(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/SwaggerConfiguration.java"
    source.parent.mkdir(parents=True)
    source.write_text("import springfox.documentation.spring.web.plugins.Docket;\n")
    model = TestModel(
        custom_output_args={
            "diagnosis": "Springfox remains",
            "summary": "Converted Docket",
            "files": [
                {
                    "path": "src/main/java/SwaggerConfiguration.java",
                    "content": "class SwaggerConfiguration {}\n",
                }
            ],
        }
    )
    repairer = AiRepairAgent(Settings(repair_mode=RepairMode.CONFIGURED_PROVIDER))
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
        reason="Upgrade Boot.",
        evidence=["boot"],
        expected_benefit="Boot 2.7.",
        priority="high",
        confidence=1,
    )

    with repairer.override(model=model):
        result = repairer.repair(
            tmp_path,
            step,
            VerificationResult(command=["mvn", "test"], exit_code=1, duration_seconds=0),
        )

    assert result.green
    assert source.read_text() == "class SwaggerConfiguration {}\n"


def test_ai_repair_rejects_a_noop_replacement(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/SwaggerConfiguration.java"
    source.parent.mkdir(parents=True)
    original = "import springfox.documentation.spring.web.plugins.Docket;\n"
    source.write_text(original)
    model = TestModel(
        custom_output_args={
            "diagnosis": "No change",
            "summary": "No change",
            "files": [{"path": str(source.relative_to(tmp_path)), "content": original}],
        }
    )
    repairer = AiRepairAgent(Settings(repair_mode=RepairMode.CONFIGURED_PROVIDER))
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
        reason="Convert Springfox.",
        evidence=["springfox"],
        expected_benefit="Use Springdoc.",
        priority="high",
        confidence=1,
    )

    with repairer.override(model=model):
        result = repairer.repair(
            tmp_path,
            step,
            VerificationResult(command=["mvn", "test"], exit_code=1, duration_seconds=0),
        )

    assert not result.green
    assert "did not change any files" in result.stderr


def test_ai_repair_scope_uses_diagnostic_source_and_module_build_files(tmp_path: Path) -> None:
    source = tmp_path / "module/src/main/java/example/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {}\n")
    (tmp_path / "pom.xml").write_text("<project/>\n")
    (tmp_path / "module/pom.xml").write_text("<project/>\n")

    allowed = _ai_repair_files(
        tmp_path,
        RecipeRecommendation(
            recipe="org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
            reason="Upgrade Boot.",
            evidence=["boot"],
            expected_benefit="Boot 3.",
            priority="high",
            confidence=1,
        ),
        VerificationResult(
            command=["mvn", "test"],
            exit_code=1,
            stdout=f"[ERROR] {source}:[10,2] cannot find symbol",
            duration_seconds=0,
        ),
    )

    assert allowed == (
        Path("module/pom.xml"),
        Path("module/src/main/java/example/App.java"),
        Path("pom.xml"),
    )


def test_ai_repair_scope_finds_same_missing_symbol_across_modules(tmp_path: Path) -> None:
    first = tmp_path / "one/src/main/java/example/First.java"
    second = tmp_path / "two/src/main/java/example/Second.java"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("@EnableEurekaClient class First {}\n")
    second.write_text("@EnableEurekaClient class Second {}\n")
    (tmp_path / "pom.xml").write_text("<project/>\n")

    allowed = _ai_repair_files(
        tmp_path,
        RecipeRecommendation(
            recipe="org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
            reason="Upgrade Boot.",
            evidence=["boot"],
            expected_benefit="Boot 3.",
            priority="high",
            confidence=1,
        ),
        VerificationResult(
            command=["mvn", "test"],
            exit_code=1,
            stdout=f"[ERROR] {first}:[1,1] cannot find symbol\nsymbol: class EnableEurekaClient",
            duration_seconds=0,
        ),
    )

    assert Path("one/src/main/java/example/First.java") in allowed
    assert Path("two/src/main/java/example/Second.java") in allowed


def test_ai_repair_scope_includes_openapi_contract_for_springdoc(tmp_path: Path) -> None:
    contract = tmp_path / "src/main/resources/api-docs.yml"
    contract.parent.mkdir(parents=True)
    contract.write_text("openapi: 3.0.0\n")
    (tmp_path / "pom.xml").write_text("<project/>\n")

    allowed = _ai_repair_files(
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
            command=["mvn", "test"],
            exit_code=1,
            stdout="Status expected:<400> but was:<201>",
            duration_seconds=0,
        ),
    )

    assert allowed == (Path("pom.xml"), Path("src/main/resources/api-docs.yml"))


def test_ai_repair_context_keeps_relevant_schema_beyond_file_limit() -> None:
    prefix = "openapi: 3.0.0\n" + ("# filler\n" * 100)
    contract = (
        prefix
        + "components:\n  schemas:\n"
        + "    PetType:\n"
        + "      type: object\n"
        + "      properties:\n"
        + "        name:\n"
        + "          type: string\n"
        + "          minLength: 1\n"
        + "      required:\n"
        + "        - id\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stdout="PetTypeRestControllerTests Status expected:<400> but was:<201>",
        duration_seconds=0,
    )

    rendered = _repair_file_context(Path("api-docs.yml"), contract, failure, 400)

    assert rendered == contract
    assert "PetType:" in rendered
    assert "minLength: 1" in rendered


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


def test_springdoc_repair_uses_swagger2_for_generated_api_model_property(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <properties>
    <openapi-generator-maven-plugin.version>5.2.1</openapi-generator-maven-plugin.version>
  </properties>
  <build>
    <plugins>
      <plugin>
        <artifactId>some-other-plugin</artifactId>
        <configuration><configOptions/></configuration>
      </plugin>
      <plugin>
        <groupId>org.openapitools</groupId>
        <artifactId>openapi-generator-maven-plugin</artifactId>
        <configuration>
          <configOptions>
            <dateLibrary>java8</dateLibrary>
          </configOptions>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout="[ERROR] symbol:   class ApiModelProperty",
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert configure_openapi_generator_for_springdoc(tmp_path, failure)
    updated = pom.read_text()
    assert "<annotationLibrary>swagger2</annotationLibrary>" in updated
    assert "<openapi-generator-maven-plugin.version>6.6.0" in updated
    assert "<groupId>io.swagger</groupId>" not in updated
    assert updated.index("<annotationLibrary>swagger2</annotationLibrary>") > updated.index(
        "<artifactId>openapi-generator-maven-plugin</artifactId>"
    )


def test_springdoc_repair_aligns_annotations_for_schema_required_mode(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <dependencies>
    <dependency>
      <groupId>org.springdoc</groupId>
      <artifactId>springdoc-openapi-ui</artifactId>
      <version>1.5.13</version>
    </dependency>
  </dependencies>
  <build><plugins><plugin>
    <artifactId>openapi-generator-maven-plugin</artifactId>
  </plugin></plugins></build>
</project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout=(
            "symbol:   method requiredMode()\n"
            "location: @interface io.swagger.v3.oas.annotations.media.Schema"
        ),
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert configure_openapi_generator_for_springdoc(tmp_path, failure)
    assert "<version>1.8.0</version>" in pom.read_text()


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


def test_springdoc_repair_converts_invalid_info_constructor(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/example/SwaggerConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """package example;

import java.util.Collections;
import io.swagger.v3.oas.models.info.Contact;
import io.swagger.v3.oas.models.info.Info;

class SwaggerConfig {
    private Info apiInfo() {
        return new Info(
            "Flight Booking REST API",
            "Flight Booking REST API Documentation",
            "1.0",
            "Terms of service",
            new Contact().name("Name").url("https://example.com").email("a@example.com"),
            "License of API",
            "https://example.com/license",
            Collections.emptyList());
    }
}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout=(
            "constructor Info in class io.swagger.v3.oas.models.info.Info "
            "cannot be applied to given types"
        ),
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert repair_springdoc_info_constructor(tmp_path, failure) == (
        "src/main/java/example/SwaggerConfig.java",
    )
    updated = source.read_text()
    assert "return new Info()" in updated
    assert '.title("Flight Booking REST API")' in updated
    assert '.version("1.0")' in updated
    assert "new License().name" in updated
    assert "import io.swagger.v3.oas.models.info.License;" in updated
    assert "Collections" not in updated


def test_deterministic_repair_handles_info_constructor_without_ai(tmp_path: Path) -> None:
    source = tmp_path / "SwaggerConfig.java"
    source.write_text(
        """import io.swagger.v3.oas.models.info.Info;
class SwaggerConfig {
    Info info() {
        return new Info("title", "description", "1", "terms", null,
            "license", "license-url", java.util.Collections.emptyList());
    }
}
"""
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stdout="constructor Info in class io.swagger.v3.oas.models.info.Info",
        duration_seconds=0,
    )

    result = DeterministicMigrationRepairer().repair(
        tmp_path,
        RecipeRecommendation(
            recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
            reason="upgrade",
            evidence=["test"],
            expected_benefit="upgrade",
            priority="high",
            confidence=1,
        ),
        failure,
    )

    assert result.green
    assert "return new Info()" in source.read_text()


def test_boot_27_repair_replaces_bare_validation_api_with_boot_starter(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <dependencies>
    <dependency>
      <groupId>javax.validation</groupId>
      <artifactId>validation-api</artifactId>
    </dependency>
  </dependencies>
</project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "clean", "test"],
        exit_code=1,
        stdout="package org.hibernate.validator.constraints does not exist",
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert configure_boot_validation_starter(tmp_path, failure)
    updated = pom.read_text()
    assert "javax.validation" not in updated
    assert "<artifactId>validation-api</artifactId>" not in updated
    assert "<artifactId>spring-boot-starter-validation</artifactId>" in updated


def test_deterministic_boot_27_repair_configures_validation_starter(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project><dependencies>
  <dependency>
    <groupId>javax.validation</groupId>
    <artifactId>validation-api</artifactId>
  </dependency>
</dependencies></project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "clean", "test"],
        exit_code=1,
        stderr="package org.hibernate.validator.constraints does not exist",
        duration_seconds=0,
    )

    result = DeterministicMigrationRepairer().repair(
        tmp_path,
        RecipeRecommendation(
            recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
            reason="upgrade",
            evidence=["test"],
            expected_benefit="upgrade",
            priority="high",
            confidence=1,
        ),
        failure,
    )

    assert result.green
    assert "managed validation starter" in result.stdout
    assert "spring-boot-starter-validation" in pom.read_text()


def test_deterministic_boot_23_repair_adds_missing_validation_starter(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        "<project><dependencies>\n"
        "  <dependency>\n"
        "    <groupId>org.springframework.boot</groupId>\n"
        "    <artifactId>spring-boot-starter-web</artifactId>\n"
        "  </dependency>\n"
        "</dependencies></project>\n"
    )
    failure = VerificationResult(
        command=["./mvnw", "clean", "test"],
        exit_code=1,
        stderr="package javax.validation does not exist\nsymbol: class Valid",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_3",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.3",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)
    assert result.green
    assert "spring-boot-starter-validation" in pom.read_text()


def test_validation_starter_is_added_to_project_dependencies_not_dependency_management(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <dependencyManagement>
    <dependencies>
      <dependency><groupId>com.example</groupId><artifactId>managed</artifactId></dependency>
    </dependencies>
  </dependencyManagement>
  <dependencies>
    <dependency><groupId>com.example</groupId><artifactId>used</artifactId></dependency>
  </dependencies>
</project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stderr="package javax.validation does not exist",
        duration_seconds=0,
    )

    assert configure_boot_validation_starter(tmp_path, failure)
    updated = pom.read_text()
    management, dependencies = updated.split("</dependencyManagement>", maxsplit=1)
    assert "spring-boot-starter-validation" not in management
    assert "spring-boot-starter-validation" in dependencies


def test_hibernate5_runtime_repair_removes_unused_hibernate4_user_type_bridges(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project><dependencies>
  <dependency>
    <groupId>org.jadira.usertype</groupId>
    <artifactId>usertype.core</artifactId>
    <version>3.2.0.GA</version>
  </dependency>
  <dependency>
    <groupId>joda-time</groupId>
    <artifactId>joda-time-hibernate</artifactId>
    <version>1.3</version>
  </dependency>
  <dependency>
    <groupId>joda-time</groupId>
    <artifactId>joda-time</artifactId>
  </dependency>
</dependencies></project>
"""
    )
    source = tmp_path / "src/main/java/example/Example.java"
    source.parent.mkdir(parents=True)
    source.write_text("package example; class Example {}\n")
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stderr=(
            "java.lang.AbstractMethodError\n"
            "at org.hibernate.internal.SessionFactoryImpl.<init>(SessionFactoryImpl.java:281)"
        ),
        duration_seconds=0,
    )

    removed = remove_legacy_hibernate4_integrations(tmp_path, failure)

    assert removed == (
        "org.jadira.usertype:usertype.core",
        "joda-time:joda-time-hibernate",
    )
    updated = pom.read_text()
    assert "usertype.core" not in updated
    assert "joda-time-hibernate" not in updated
    assert "<artifactId>joda-time</artifactId>" in updated


def test_hibernate5_runtime_failure_is_routed_to_deterministic_repair(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project><dependencies><dependency>
  <groupId>org.jadira.usertype</groupId>
  <artifactId>usertype.core</artifactId>
</dependency></dependencies></project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stderr=(
            "java.lang.AbstractMethodError\n"
            "at org.hibernate.internal.SessionFactoryImpl.<init>(SessionFactoryImpl.java:281)"
        ),
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)


def test_boot3_repair_migrates_removed_hibernate_session_factory_and_query_setter(
    tmp_path: Path,
) -> None:
    application = tmp_path / "src/main/java/example/Application.java"
    application.parent.mkdir(parents=True)
    application.write_text(
        """package example;
import org.springframework.orm.jpa.vendor.HibernateJpaSessionFactoryBean;
class Application {
    public HibernateJpaSessionFactoryBean sessionFactory() {
        return new HibernateJpaSessionFactoryBean();
    }
}
"""
    )
    controller = tmp_path / "src/main/java/example/ProductController.java"
    controller.write_text(
        """package example;
class ProductController {
    void find(org.hibernate.query.Query query) {
        query.setLong("product_id", 1L);
    }
}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stderr=(
            "cannot find symbol\n"
            "symbol:   class HibernateJpaSessionFactoryBean\n"
            "symbol:   method setLong(java.lang.String,long)"
        ),
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert migrate_removed_hibernate6_apis(tmp_path, failure) == (
        "src/main/java/example/Application.java",
        "src/main/java/example/ProductController.java",
    )
    updated_application = application.read_text()
    assert "import jakarta.persistence.EntityManagerFactory;" in updated_application
    assert "public SessionFactory sessionFactory(EntityManagerFactory" in updated_application
    assert "entityManagerFactory.unwrap(SessionFactory.class)" in updated_application
    assert '.setParameter("product_id", 1L)' in controller.read_text()


@pytest.mark.parametrize("minor", [3, 4, 5, 6, 7])
def test_validation_repair_covers_boot_2_bridge_releases(tmp_path: Path, minor: int) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <dependencies>
    <dependency>
      <groupId>javax.validation</groupId>
      <artifactId>validation-api</artifactId>
    </dependency>
  </dependencies>
</project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "clean", "test"],
        exit_code=1,
        stderr="package org.hibernate.validator.constraints does not exist",
        duration_seconds=0,
    )

    result = DeterministicMigrationRepairer().repair(
        tmp_path,
        RecipeRecommendation(
            recipe=f"org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_{minor}",
            reason="upgrade",
            evidence=["test"],
            expected_benefit="upgrade",
            priority="high",
            confidence=1,
        ),
        failure,
    )

    assert result.green
    assert "spring-boot-starter-validation" in pom.read_text()


def test_boot_26_repair_breaks_password_encoder_configuration_cycle(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/WebSecurityConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """class WebSecurityConfig {
    @Bean
    public BCryptPasswordEncoder bcryptEncoder() {
        return new BCryptPasswordEncoder();
    }
}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "clean", "test"],
        exit_code=1,
        stderr=(
            "BeanCurrentlyInCreationException: Error creating bean with name 'webSecurityConfig'"
        ),
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert make_password_encoder_bean_independent(tmp_path, failure) == (
        "src/main/java/example/WebSecurityConfig.java",
    )
    assert "public static BCryptPasswordEncoder bcryptEncoder()" in source.read_text()


def test_deterministic_boot_26_repair_handles_security_configuration_cycle(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/WebSecurityConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """class WebSecurityConfig {
    @Bean
    public BCryptPasswordEncoder bcryptEncoder() {
        return new BCryptPasswordEncoder();
    }
}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "clean", "test"],
        exit_code=1,
        stdout="BeanCurrentlyInCreationException: webSecurityConfig",
        duration_seconds=0,
    )

    result = DeterministicMigrationRepairer().repair(
        tmp_path,
        RecipeRecommendation(
            recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_6",
            reason="upgrade",
            evidence=["test"],
            expected_benefit="upgrade",
            priority="high",
            confidence=1,
        ),
        failure,
    )

    assert result.green
    assert "password-encoder bean independent" in result.stdout
    assert "public static BCryptPasswordEncoder bcryptEncoder()" in source.read_text()


def test_security_58_repair_uses_explicit_ant_path_request_matchers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/WebSecurityConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """package example;

import org.springframework.security.config.annotation.web.builders.HttpSecurity;

class WebSecurityConfig {
    void configure(HttpSecurity http) throws Exception {
        http.authorizeHttpRequests(requests -> requests
            .requestMatchers("/create/**", "/edit/**").hasRole("USER")
            .requestMatchers("/**").permitAll());
    }
}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "clean", "test"],
        exit_code=1,
        stderr=(
            "method requestMatchers cannot be applied; required: RequestMatcher[]; "
            "java.lang.String cannot be converted to "
            "org.springframework.security.web.util.matcher.RequestMatcher"
        ),
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert use_explicit_ant_path_request_matchers(tmp_path, failure) == (
        "src/main/java/example/WebSecurityConfig.java",
    )
    updated = source.read_text()
    assert "import org.springframework.security.web.util.matcher.AntPathRequestMatcher;" in updated
    assert (
        '.requestMatchers(new AntPathRequestMatcher("/create/**"), '
        'new AntPathRequestMatcher("/edit/**"))' in updated
    )
    assert '.requestMatchers(new AntPathRequestMatcher("/**"))' in updated


def test_deterministic_security_58_repair_is_recipe_scoped(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/example/WebSecurityConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text('class WebSecurityConfig { void x() { registry.requestMatchers("/**"); } }\n')
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout=(
            "method requestMatchers cannot be applied; "
            "java.lang.String cannot be converted to "
            "org.springframework.security.web.util.matcher.RequestMatcher"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.security5.UpgradeSpringSecurity_5_8",
        reason="upgrade",
        evidence=["test"],
        expected_benefit="upgrade",
        priority="high",
        confidence=1,
    )

    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    assert "explicit AntPathRequestMatcher" in result.stdout
    assert 'new AntPathRequestMatcher("/**")' in source.read_text()


def test_deterministic_springfox_repair_removes_residual_configuration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/SwaggerConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """package example;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import springfox.documentation.spring.data.rest.configuration.SpringDataRestConfiguration;
import springfox.documentation.swagger2.annotations.EnableSwagger2WebMvc;
@Configuration
@EnableSwagger2WebMvc
@Import(SpringDataRestConfiguration.class)
class SwaggerConfig {}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout=(
            "package springfox.documentation.spring.data.rest.configuration does not exist\n"
            "package springfox.documentation.swagger2.annotations does not exist"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
        reason="convert",
        evidence=["springfox"],
        expected_benefit="springdoc",
        priority="high",
        confidence=1,
    )

    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    assert "Removed obsolete Springfox" in result.stdout
    updated = source.read_text()
    assert "springfox" not in updated


def test_deterministic_springfox_repair_deletes_unconverted_docket_config(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/MySwaggerConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """package example;
import org.springframework.context.annotation.Configuration;
import springfox.documentation.builders.PathSelectors;
import springfox.documentation.spring.web.plugins.Docket;
@Configuration
class MySwaggerConfig {
    // TODO: transformation of Docket bean to OpenAPI
    Docket api() { return null; }
}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout="package springfox.documentation.builders does not exist",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
        reason="Replace Springfox.",
        evidence=["springfox"],
        expected_benefit="Modern API documentation.",
        priority="high",
        confidence=1,
    )

    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    assert "Removed obsolete Springfox" in result.stdout
    assert not source.exists()


def test_deterministic_springfox_repair_removes_handler_provider_workaround(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/ApplicationSwaggerConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """package example;
import org.springframework.beans.BeansException;
import org.springframework.beans.factory.config.BeanPostProcessor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.ReflectionUtils;
import org.springframework.web.servlet.mvc.method.RequestMappingInfoHandlerMapping;
import springfox.documentation.spring.web.plugins.WebFluxRequestHandlerProvider;
import springfox.documentation.spring.web.plugins.WebMvcRequestHandlerProvider;
import java.lang.reflect.Field;
import java.util.List;

@Configuration
class ApplicationSwaggerConfig {
    /** Springfox workaround required by Spring Boot 2.6 */
    @Bean
    public static BeanPostProcessor springfoxHandlerProviderBeanPostProcessor() {
        return new BeanPostProcessor() {
            public Object postProcessAfterInitialization(Object bean, String name)
                    throws BeansException {
                if (bean instanceof WebMvcRequestHandlerProvider
                        || bean instanceof WebFluxRequestHandlerProvider) {
                    Field field = ReflectionUtils.findField(bean.getClass(), "handlerMappings");
                    List<RequestMappingInfoHandlerMapping> mappings = null;
                }
                return bean;
            }
        };
    }
}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout=("package springfox.documentation.spring.web.plugins does not exist"),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
        reason="Replace Springfox.",
        evidence=["springfox"],
        expected_benefit="Modern documentation.",
        priority="high",
        confidence=1,
    )

    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    updated = source.read_text()
    assert "springfoxHandlerProviderBeanPostProcessor" not in updated
    assert "springfox.documentation.spring.web.plugins" not in updated
    assert "BeanPostProcessor" not in updated
    assert "@Configuration" in updated
    assert "@Import" not in updated
    assert "annotation.Import" not in updated


def test_boot_30_repair_removes_legacy_method_security_superclass(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/MethodSecurityConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package example;\n\n"
        "import org.springframework.context.annotation.Configuration;\n"
        "import org.springframework.security.config.annotation.method."
        "configuration.EnableMethodSecurity;\n"
        "import org.springframework.security.config.annotation.method."
        "configuration.GlobalMethodSecurityConfiguration;\n\n"
        "@Configuration\n"
        "@EnableMethodSecurity(securedEnabled = true, jsr250Enabled = true)\n"
        "public class MethodSecurityConfig extends GlobalMethodSecurityConfiguration {\n"
        "}\n"
    )
    failure = VerificationResult(
        command=["./mvnw", "clean", "test"],
        exit_code=1,
        stderr=(
            "Factory method 'methodSecurityMetadataSource' threw exception: "
            "EnableGlobalMethodSecurity is required"
        ),
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert remove_legacy_global_method_security_inheritance(tmp_path, failure) == (
        "src/main/java/example/MethodSecurityConfig.java",
    )
    updated = source.read_text()
    assert "public class MethodSecurityConfig {" in updated
    assert "GlobalMethodSecurityConfiguration" not in updated


def test_deterministic_boot_27_repair_updates_repository_rest_signature(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/MyDataRestConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """package example;
import org.springframework.data.rest.core.config.RepositoryRestConfiguration;
import org.springframework.data.rest.webmvc.config.RepositoryRestConfigurer;
class MyDataRestConfig implements RepositoryRestConfigurer {
    @Override
    public void configureRepositoryRestConfiguration(RepositoryRestConfiguration config) {
    }
}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout="method does not override or implement a method from a supertype",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.7",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    updated = source.read_text()
    assert "RepositoryRestConfiguration config, CorsRegistry cors" in updated
    assert "import org.springframework.web.servlet.config.annotation.CorsRegistry;" in updated


def test_deterministic_repair_replaces_removed_spring_data_find_one_id(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/ProductService.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class ProductService {\n"
        "    Product get(long id) { return repository.findOne(id); }\n"
        "    Product find(Product probe) { return repository.findOne(probe); }\n"
        "}\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr=(
            f"{source}:[2,47] method findOne in interface "
            "org.springframework.data.repository.query."
            "QueryByExampleExecutor<T> cannot be applied to given types; "
            "argument mismatch; long cannot be converted to "
            "org.springframework.data.domain.Example<S>"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_0",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.0",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    updated = source.read_text()
    assert "repository.findById(id).orElse(null)" in updated
    assert "repository.findOne(probe)" in updated


def test_deterministic_repair_replaces_removed_boot_2_apis(tmp_path: Path) -> None:
    config = tmp_path / "src/main/java/example/DatabaseConfiguration.java"
    config.parent.mkdir(parents=True)
    config.write_text(
        "import org.springframework.boot.bind.RelaxedPropertyResolver;\n"
        "import org.springframework.core.env.Environment;\n"
        "class DatabaseConfiguration {\n"
        " private RelaxedPropertyResolver propertyResolver;\n"
        " void setEnvironment(Environment env) {\n"
        '  this.propertyResolver = new RelaxedPropertyResolver(env, "spring.datasource.");\n'
        " }\n"
        ' String url() { return propertyResolver.getProperty("url"); }\n'
        "}\n"
    )
    application = tmp_path / "src/main/java/example/Application.java"
    application.write_text("app.setShowBanner(false);\n")
    initializer = tmp_path / "src/main/java/example/ApplicationWebXml.java"
    initializer.write_text(
        "import org.springframework.boot.context.web.SpringBootServletInitializer;\n"
        "class ApplicationWebXml extends SpringBootServletInitializer {\n"
        " Object configure(Object application) {\n"
        "  return application.showBanner(false);\n"
        " }\n"
        "}\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr=(
            "package org.springframework.boot.bind does not exist\n"
            "package org.springframework.boot.context.web does not exist\n"
            "symbol:   method setShowBanner(boolean)\n"
            "symbol:   method showBanner(boolean)"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_0",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.0",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    updated_config = config.read_text()
    assert "RelaxedPropertyResolver" not in updated_config
    assert "private Environment propertyResolver;" in updated_config
    assert "this.propertyResolver = env;" in updated_config
    assert 'getProperty("spring.datasource.url")' in updated_config
    assert "setBannerMode(org.springframework.boot.Banner.Mode.OFF)" in application.read_text()
    updated_initializer = initializer.read_text()
    assert (
        "org.springframework.boot.web.servlet.support.SpringBootServletInitializer"
        in updated_initializer
    )
    assert "bannerMode(org.springframework.boot.Banner.Mode.OFF)" in updated_initializer


def test_deterministic_repair_preserves_legacy_mockito_null_matching(tmp_path: Path) -> None:
    source = tmp_path / "src/test/java/example/RedisCacheTest.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import static org.mockito.Mockito.*;\n"
        "class RedisCacheTest {\n"
        " void setup() { when(mapper.readValue(anyString(), any())).thenReturn(value); }\n"
        "}\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr=(
            "Tests run: 1, Failures: 0, Errors: 1\n"
            "java.lang.NullPointerException\n"
            " at example.RedisCacheTest.testGetItem(RedisCacheTest.java:8)"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_0",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.0",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    assert "nullable(String.class)" in source.read_text()


@pytest.mark.parametrize("artifact", ["mockito-all", "mockito-core"])
def test_deterministic_repair_removes_conflicting_explicit_mockito(
    tmp_path: Path, artifact: str
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        "<project><dependencies>\n"
        "  <dependency>\n"
        "    <groupId>org.mockito</groupId>\n"
        f"    <artifactId>{artifact}</artifactId>\n"
        "    <version>2.0.2-beta</version>\n"
        "    <scope>test</scope>\n"
        "  </dependency>\n"
        "</dependencies></project>\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr="java.lang.NoClassDefFoundError: net/bytebuddy/utility/GraalImageCode",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_4",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.4",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    assert artifact not in pom.read_text()


def test_deterministic_repair_migrates_security_adapter(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/example/WebSecurityConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import org.springframework.context.annotation.Bean;\n"
        "import org.springframework.security.config.annotation.authentication.builders."
        "AuthenticationManagerBuilder;\n"
        "import org.springframework.security.config.annotation.web.builders.HttpSecurity;\n"
        "import org.springframework.security.config.annotation.web.configuration."
        "WebSecurityConfigurerAdapter;\n"
        "class WebSecurityConfig extends WebSecurityConfigurerAdapter {\n"
        " /*~~(Migrate manually)~~>*/@Override\n"
        " protected void configure(AuthenticationManagerBuilder auth) throws Exception {\n"
        "  auth.userDetailsService(userDetailsService);\n"
        " }\n"
        " @Override\n"
        " protected void configure(HttpSecurity http) throws Exception {\n"
        '  http.authorizeRequests().antMatchers("/").permitAll();\n'
        " }\n"
        "}\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr="cannot find symbol\n  symbol: class WebSecurityConfigurerAdapter",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 3.0",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    updated = source.read_text()
    assert "WebSecurityConfigurerAdapter" not in updated
    assert "SecurityFilterChain securityFilterChain" in updated
    assert 'authorizeHttpRequests().requestMatchers("/")' in updated
    assert "return http.build();" in updated


def test_deterministic_repair_finishes_security_6_authentication_manager(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/SecurityConfig.java"
    source.parent.mkdir(parents=True)
    marker = "/*~~(Migrate manually based on migration guide)~~>*/"
    source.write_text(
        "import org.springframework.context.annotation.Bean;\n"
        "import org.springframework.security.authentication.AuthenticationManager;\n"
        "import org.springframework.security.config.BeanIds;\n"
        "import org.springframework.security.config.annotation.authentication.builders."
        "AuthenticationManagerBuilder;\n"
        "import org.springframework.security.web.SecurityFilterChain;\n"
        "class SecurityConfig {\n"
        f" {marker}{marker}public void configure(AuthenticationManagerBuilder auth) "
        "throws Exception {\n"
        "  auth.userDetailsService(userDetailsService);\n"
        " }\n"
        f" {marker}{marker}@Bean(BeanIds.AUTHENTICATION_MANAGER)\n"
        " public AuthenticationManager authenticationManagerBean() throws Exception {\n"
        "  return super.authenticationManagerBean();\n"
        " }\n"
        "}\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr="cannot find symbol\n  symbol:   class AuthenticationManagerBuilder",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 3.0",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    updated = source.read_text()
    assert "AuthenticationManagerBuilder" not in updated
    assert "BeanIds" not in updated
    assert "AuthenticationConfiguration" in updated
    assert "configuration.getAuthenticationManager()" in updated


def test_deterministic_repair_allows_legacy_circular_references(tmp_path: Path) -> None:
    properties = tmp_path / "module/src/test/resources/application.properties"
    properties.parent.mkdir(parents=True)
    properties.write_text("spring.jpa.show-sql=false\n")
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr=(
            "BeanCurrentlyInCreationException: Requested bean is currently in creation: "
            "Is there an unresolvable circular reference?"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_6",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.6",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    assert "spring.main.allow-circular-references=true" in properties.read_text()


def test_deterministic_repair_migrates_removed_boot_test_annotations(tmp_path: Path) -> None:
    source = tmp_path / "src/test/java/example/HomeControllerTest.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import org.springframework.boot.test.IntegrationTest;\n"
        "import org.springframework.boot.test.SpringApplicationConfiguration;\n"
        "import org.junit.Test;\n"
        "@SpringApplicationConfiguration(classes = Application.class)\n"
        "@IntegrationTest\n"
        "class HomeControllerTest {}\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr="cannot find symbol\n  symbol: class SpringApplicationConfiguration",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_0",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.0",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    updated = source.read_text()
    assert "import org.springframework.boot.test.context.SpringBootTest;" in updated
    assert "@SpringBootTest(classes = Application.class)" in updated
    assert "IntegrationTest" not in updated


def test_deterministic_repair_migrates_hateoas_1_apis(tmp_path: Path) -> None:
    resource = tmp_path / "src/main/java/example/ProductResource.java"
    resource.parent.mkdir(parents=True)
    resource.write_text(
        "import org.springframework.hateoas.ResourceSupport;\n"
        "class ProductResource extends ResourceSupport {}\n"
    )
    controller = tmp_path / "src/main/java/example/ProductController.java"
    controller.write_text(
        "import static org.springframework.hateoas.mvc.ControllerLinkBuilder.linkTo;\n"
        "class ProductController { ResourceSupport view() { return null; } }\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr=(
            "package org.springframework.hateoas.mvc does not exist\nsymbol: class ResourceSupport"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_2",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.2",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    assert "extends RepresentationModel" in resource.read_text()
    assert "hateoas.server.mvc.WebMvcLinkBuilder.linkTo" in controller.read_text()
    assert "RepresentationModel view()" in controller.read_text()


def test_deterministic_repair_adds_swagger_3_annotations_to_affected_module(
    tmp_path: Path,
) -> None:
    root_pom = tmp_path / "pom.xml"
    root_pom.write_text("<project><modules><module>model</module></modules></project>\n")
    module_pom = tmp_path / "model/pom.xml"
    module_pom.parent.mkdir()
    module_pom.write_text("<project><dependencies>\n</dependencies></project>\n")
    source = tmp_path / "model/src/main/java/example/Address.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import io.swagger.v3.oas.annotations.media.Schema;\n"
        "class Address { @Schema String city; }\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr="package io.swagger.v3.oas.annotations.media does not exist",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_6",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.6",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    assert "io.swagger.core.v3" in module_pom.read_text()
    assert "io.swagger.core.v3" not in root_pom.read_text()


def test_deterministic_repair_removes_thymeleaf_2_only_joda_dialect(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        "<project><properties>\n"
        "  <jodatime-thymeleaf.version>1.3</jodatime-thymeleaf.version>\n"
        "</properties><dependencies>\n"
        "  <dependency>\n"
        "    <groupId>uk.co.gcwilliams</groupId>\n"
        "    <artifactId>jodatime-thymeleaf-dialect</artifactId>\n"
        "    <version>${jodatime-thymeleaf.version}</version>\n"
        "  </dependency>\n"
        "</dependencies></project>\n"
    )
    source = tmp_path / "src/main/java/example/LocaleConfiguration.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import org.springframework.context.annotation.Bean;\n"
        "import uk.co.gcwilliams.jodatime.thymeleaf.JodaTimeDialect;\n"
        "class LocaleConfiguration {\n"
        " /**\n"
        "  * Joda Time to Thymeleaf converter\n"
        "  */\n"
        " @Bean\n"
        " public JodaTimeDialect jodaTimeDialect() {\n"
        "  return new JodaTimeDialect();\n"
        " }\n"
        " @Bean Object localeResolver() { return null; }\n"
        "}\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr="java.lang.NoClassDefFoundError: org/thymeleaf/dialect/IExpressionEnhancingDialect",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_0",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.0",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    assert "jodatime-thymeleaf" not in pom.read_text()
    updated = source.read_text()
    assert "JodaTimeDialect" not in updated
    assert "localeResolver" in updated


def test_deterministic_repair_makes_list_derived_query_explicit(tmp_path: Path) -> None:
    repository_source = tmp_path / "src/main/java/example/PostRepository.java"
    repository_source.parent.mkdir(parents=True)
    repository_source.write_text(
        "interface PostRepository {\n"
        " Page<Post> findByTags(List<Tag> tags, Pageable pageable);\n"
        "}\n"
    )
    service_source = tmp_path / "src/main/java/example/PostService.java"
    service_source.write_text(
        "class PostService {\n"
        " void find(List<Tag> tags) { repository.findByTags(tags, pageable); }\n"
        "}\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr=(
            "Failed to create query for method public abstract Page "
            "example.PostRepository.findByTags(java.util.List,Pageable)! "
            "Operator SIMPLE_PROPERTY on tags requires a scalar argument, "
            "found interface java.util.List"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_2",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.2",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    assert "findByTagsIn(List<Tag>" in repository_source.read_text()
    assert "repository.findByTagsIn(tags, pageable)" in service_source.read_text()


def test_deterministic_repair_disambiguates_custom_repository_get_by_id(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sm-core/src/main/java/example/ProductService.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class ProductService {\n"
        " Product get(Long id) { return productRepository.getById(id); }\n"
        "}\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stderr=(
            f"{source}:[2,49] reference to getById is ambiguous\n"
            "both method getById(java.lang.Long) in "
            "example.ProductRepositoryCustom and method getById(ID) in "
            "org.springframework.data.jpa.repository.JpaRepository match"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_5",
        reason="upgrade",
        evidence=["boot"],
        expected_benefit="Boot 2.5",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert DeterministicMigrationRepairer().repair(tmp_path, step, failure).green
    assert "((example.ProductRepositoryCustom) productRepository).getById(id)" in source.read_text()


def test_deterministic_boot_30_repair_finishes_method_security_migration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/MethodSecurityConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import org.springframework.security.config.annotation.method."
        "configuration.EnableMethodSecurity;\n"
        "import org.springframework.security.config.annotation.method."
        "configuration.GlobalMethodSecurityConfiguration;\n"
        "@EnableMethodSecurity\n"
        "class MethodSecurityConfig extends GlobalMethodSecurityConfiguration {}\n"
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout="EnableGlobalMethodSecurity is required",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        reason="upgrade",
        evidence=["test"],
        expected_benefit="upgrade",
        priority="high",
        confidence=1,
    )

    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    assert "legacy GlobalMethodSecurityConfiguration inheritance" in result.stdout
    assert "extends GlobalMethodSecurityConfiguration" not in source.read_text()


def test_deterministic_repair_removes_obsolete_junit_platform_provider(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.2.5</version>
        <dependencies>
          <dependency>
            <groupId>org.junit.platform</groupId>
            <artifactId>junit-platform-surefire-provider</artifactId>
            <version>1.1.0</version>
          </dependency>
        </dependencies>
      </plugin>
    </plugins>
  </build>
</project>
"""
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stdout=(
            "org.apache.maven.surefire.api.testset.TestSetFailedException "
            "was missing while executing maven-surefire-plugin:3.2.5"
        ),
        duration_seconds=0,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    assert remove_obsolete_junit_platform_surefire_provider(tmp_path, failure)
    updated = pom.read_text()
    assert "junit-platform-surefire-provider" not in updated
    assert "<dependencies>" not in updated
    assert "<version>3.2.5</version>" in updated


def test_deterministic_repair_handles_surefire_console_logger_failure(
    tmp_path: Path,
) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.2.5</version>
        <dependencies>
          <dependency>
            <groupId>org.junit.platform</groupId>
            <artifactId>junit-platform-surefire-provider</artifactId>
            <version>1.1.0</version>
          </dependency>
        </dependencies>
      </plugin>
    </plugins>
  </build>
</project>
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "clean", "test"],
        exit_code=1,
        stdout=(
            "java.lang.NoClassDefFoundError: "
            "org/apache/maven/plugin/surefire/log/api/ConsoleLogger\n"
            "Caused by: java.lang.ClassNotFoundException: "
            "org.apache.maven.plugin.surefire.log.api.ConsoleLogger"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_1",
        reason="Upgrade Spring Boot.",
        evidence=["spring boot"],
        expected_benefit="Spring Boot 3.1.",
        priority="high",
        confidence=1,
    )

    assert springdoc_repair_applicable(tmp_path, failure)
    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    assert "junit-platform-surefire-provider" not in pom.read_text()


def test_deterministic_repair_makes_private_bean_method_public(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/example/SecurityConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """class SecurityConfig {
    @Bean
    private PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }
}
"""
    )
    failure = VerificationResult(
        command=["./mvnw", "test"],
        exit_code=1,
        stdout="@Bean method 'passwordEncoder' must not be private or final",
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
        reason="Upgrade Spring Boot.",
        evidence=["spring boot"],
        expected_benefit="Spring Boot 2.7.",
        priority="high",
        confidence=1,
    )

    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    assert "public PasswordEncoder passwordEncoder()" in source.read_text()


def test_deterministic_repair_adds_ids_to_h2_comment_seed_rows(tmp_path: Path) -> None:
    seed = tmp_path / "src/main/resources/sql/import-h2.sql"
    seed.parent.mkdir(parents=True)
    seed.write_text(
        "INSERT INTO COMMENT (post_id, user_id, body, create_date)\n"
        "VALUES (1, 2, 'first', CURRENT_TIMESTAMP());\n"
        "INSERT INTO COMMENT (post_id, user_id, body, create_date)\n"
        "VALUES (3, 4, 'second', CURRENT_TIMESTAMP());\n"
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stdout='NULL not allowed for column "COMMENT_ID"',
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_0",
        reason="Upgrade Spring Boot.",
        evidence=["spring boot"],
        expected_benefit="Spring Boot 2.",
        priority="high",
        confidence=1,
    )

    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    updated = seed.read_text()
    assert "COMMENT (comment_id, post_id" in updated
    assert "VALUES (1, 1, 2" in updated
    assert "VALUES (2, 3, 4" in updated


def test_deterministic_repair_restores_fastjson1_redis_configuration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/main/java/example/CacheConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """import com.alibaba.fastjson.support.spring.FastJsonRedisSerializer;
import com.alibaba.fastjson2.reader.ObjectReaderProvider;
class CacheConfig {
  Object value() {
    ObjectReaderProvider globalInstance = ObjectReaderProvider.getGlobalInstance();
    return Feature.WriteClassName;
  }
}
"""
    )
    failure = VerificationResult(
        command=["mvn", "test"],
        exit_code=1,
        stdout=(
            "ObjectReaderProvider cannot be converted to com.alibaba.fastjson.parser.ParserConfig"
        ),
        duration_seconds=0,
    )
    step = RecipeRecommendation(
        recipe="org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        reason="Upgrade Spring Boot.",
        evidence=["spring boot"],
        expected_benefit="Spring Boot 3.",
        priority="high",
        confidence=1,
    )

    result = DeterministicMigrationRepairer().repair(tmp_path, step, failure)

    assert result.green
    updated = source.read_text()
    assert "ParserConfig globalInstance = ParserConfig.getGlobalInstance()" in updated
    assert "SerializerFeature.WriteClassName" in updated
    assert "ObjectReaderProvider" not in updated


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
            "target/generated-sources/openapi/Dto.java: package javax.validation does not exist"
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
    repairer = MigrationRepairChain(
        DeterministicMigrationRepairer(),
        AiMigrationRepairer(Settings(repair_mode=RepairMode.CONFIGURED_PROVIDER)),
    )
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
            "Status expected:<400> but was:<201>\nshouldDeletePet:213 » InvalidDataAccessApiUsage"
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
            "PetTypeRestControllerTests.testCreatePetTypeError Status expected:<400> but was:<201>"
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
