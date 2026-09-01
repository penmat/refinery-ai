from pathlib import Path
from types import SimpleNamespace

import pytest

from refinery_ai.config import Settings
from refinery_ai.migrations.ai_repair import (
    AiMigrationRepairer,
    DeterministicMigrationRepairer,
    configure_boot3_test_compatibility,
    configure_boot_validation_starter,
    configure_openapi_generator_for_springdoc,
    make_password_encoder_bean_independent,
    remove_legacy_global_method_security_inheritance,
    remove_obsolete_junit_platform_surefire_provider,
    repair_springdoc_info_constructor,
    springdoc_repair_applicable,
    springdoc_repair_files,
    use_explicit_ant_path_request_matchers,
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


def test_springdoc_repair_converts_invalid_info_constructor(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/example/SwaggerConfig.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''package example;

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
'''
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
        '''import io.swagger.v3.oas.models.info.Info;
class SwaggerConfig {
    Info info() {
        return new Info("title", "description", "1", "terms", null,
            "license", "license-url", java.util.Collections.emptyList());
    }
}
'''
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


@pytest.mark.parametrize("minor", [3, 4, 5, 6, 7])
def test_validation_repair_covers_boot_2_bridge_releases(
    tmp_path: Path, minor: int
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
            "BeanCurrentlyInCreationException: Error creating bean with name "
            "'webSecurityConfig'"
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
    source.write_text(
        'class WebSecurityConfig { void x() { registry.requestMatchers("/**"); } }\n'
    )
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
