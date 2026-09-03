"""Bounded AI repair for deterministic migrations with semantic leftovers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field, TypeAdapter

from refinery_ai.config import Settings
from refinery_ai.llm import run_tool_loop
from refinery_ai.models import RecipeRecommendation, RepairMode, VerificationResult

_SPRINGFOX_MARKERS = ("springfox.", "transformation of Docket", "Migrate manually")
_MISSING_SWAGGER_1_ANNOTATIONS = "package io.swagger.annotations does not exist"
_MISSING_GENERATED_API_MODEL_PROPERTY = "symbol:   class ApiModelProperty"
_MISSING_SCHEMA_REQUIRED_MODE = "symbol:   method requiredMode()"
_MISSING_GENERATED_JAVAX_APIS = (
    "package javax.validation does not exist",
    "package javax.annotation does not exist",
)
_BOOT3_VALIDATION_FAILURE = "Status expected:<400> but was:<201>"
_HIBERNATE6_QUERY_FAILURE = "InvalidDataAccessApiUsage"
_JDBC_QUERY_FAILURE = "BadSqlGrammar"
_INVALID_INFO_CONSTRUCTOR = "constructor Info in class io.swagger.v3.oas.models.info.Info"
_SUREFIRE_TESTSET_FAILURE = "org.apache.maven.surefire.api.testset.TestSetFailedException"
_SUREFIRE_CONSOLE_LOGGER_FAILURE = "org.apache.maven.plugin.surefire.log.api.ConsoleLogger"
_MISSING_HIBERNATE_VALIDATOR_CONSTRAINTS = (
    "package org.hibernate.validator.constraints does not exist"
)
_CIRCULAR_SECURITY_CONFIGURATION = "BeanCurrentlyInCreationException"
_REQUEST_MATCHER_SIGNATURE_FAILURE = (
    "java.lang.String cannot be converted to "
    "org.springframework.security.web.util.matcher.RequestMatcher"
)
_LEGACY_METHOD_SECURITY_CONFIGURATION_FAILURE = "EnableGlobalMethodSecurity is required"
_PRIVATE_BEAN_METHOD_FAILURE = "@Bean method"
_H2_MISSING_COMMENT_ID = 'NULL not allowed for column "COMMENT_ID"'
_BROKEN_FASTJSON2_CACHE_CONFIG = "ObjectReaderProvider cannot be converted to"
_SPRING_DATA_FIND_ONE_ID_FAILURE = (
    "method findOne in interface org.springframework.data.repository.query.QueryByExampleExecutor"
)
_BOOT_26_RECIPE = "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_6"
_BOOT_27_RECIPE = "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7"
_BOOT_30_RECIPE = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"
_SPRING_SECURITY_58_RECIPE = "org.openrewrite.java.spring.security5.UpgradeSpringSecurity_5_8"
_SPRINGFOX_TO_SPRINGDOC_RECIPE = "org.openrewrite.java.springdoc.SpringFoxToSpringDoc"
_BOOT_2_UPGRADE_RECIPE = re.compile(
    r"org\.openrewrite\.java\.spring\.boot2\.UpgradeSpringBoot_2_[3-7]"
)


def _has_legacy_swagger_annotations(content: str) -> bool:
    return bool(
        re.search(
            r"<groupId>\s*io\.swagger\s*</groupId>\s*"
            r"<artifactId>\s*swagger-annotations\s*</artifactId>",
            content,
        )
    )


class RepairFileReplacement(BaseModel):
    path: str
    content: str


class RepairEditProposal(BaseModel):
    diagnosis: str
    summary: str
    files: list[RepairFileReplacement] = Field(min_length=1)


class MigrationRepairer(Protocol):
    def repair(
        self,
        repository: Path,
        step: RecipeRecommendation,
        failure: VerificationResult,
    ) -> VerificationResult: ...


def springdoc_repair_files(repository: Path) -> tuple[Path, ...]:
    """Return only files containing residual Springfox migration markers."""

    files: list[Path] = []
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue
        if any(marker in content for marker in _SPRINGFOX_MARKERS):
            files.append(relative)
    return tuple(sorted(files))


def make_bean_methods_non_private(repository: Path, failure: VerificationResult) -> tuple[str, ...]:
    """Expose only @Bean methods rejected by newer Spring Framework versions."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if (
        _PRIVATE_BEAN_METHOD_FAILURE not in diagnostic
        or "must not be private or final" not in diagnostic
    ):
        return ()
    repaired: list[str] = []
    for path in repository.glob("src/main/java/**/*.java"):
        content = path.read_text(errors="replace")
        updated = re.sub(
            r"(?m)^(\s*@Bean(?:\([^\n]*\))?\s*\n\s*)private(\s+)",
            r"\1public\2",
            content,
        )
        if updated != content:
            path.write_text(updated)
            repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


def add_missing_h2_seed_ids(repository: Path, failure: VerificationResult) -> tuple[str, ...]:
    """Give legacy COMMENT seed rows explicit IDs after Hibernate schema changes."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _H2_MISSING_COMMENT_ID not in diagnostic:
        return ()
    repaired: list[str] = []
    for path in repository.glob("src/main/resources/**/*.sql"):
        content = path.read_text(errors="replace")
        counter = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal counter
            counter += 1
            return f"INSERT INTO COMMENT (comment_id, {match.group(1)})\nVALUES ({counter}, "

        updated = re.sub(
            r"INSERT INTO COMMENT \((post_id, user_id, body, create_date)\)\s*\nVALUES \(",
            replace,
            content,
        )
        if updated != content:
            path.write_text(updated)
            repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


def restore_fastjson1_cache_configuration(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Undo an invalid mixed Fastjson 1/2 rewrite in Redis configuration."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _BROKEN_FASTJSON2_CACHE_CONFIG not in diagnostic:
        return ()
    repaired: list[str] = []
    for path in repository.glob("src/main/java/**/*CacheConfig.java"):
        content = path.read_text(errors="replace")
        if "FastJsonRedisSerializer" not in content or "ObjectReaderProvider" not in content:
            continue
        updated = content.replace(
            "import com.alibaba.fastjson2.reader.ObjectReaderProvider;",
            "import com.alibaba.fastjson.parser.ParserConfig;\n"
            "import com.alibaba.fastjson.serializer.SerializerFeature;",
        )
        updated = updated.replace(
            "ObjectReaderProvider globalInstance = ObjectReaderProvider.getGlobalInstance();",
            "ParserConfig globalInstance = ParserConfig.getGlobalInstance();",
        ).replace("Feature.WriteClassName", "SerializerFeature.WriteClassName")
        if updated != content:
            path.write_text(updated)
            repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


def remove_obsolete_springfox_configuration(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Remove annotations left behind after Springfox dependencies are replaced."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    missing_configuration = (
        "package springfox.documentation.spring.data.rest.configuration does not exist"
        in diagnostic
    )
    missing_annotation = (
        "package springfox.documentation.swagger2.annotations does not exist" in diagnostic
    )
    missing_handler_providers = (
        "package springfox.documentation.spring.web.plugins does not exist" in diagnostic
    )
    missing_springfox = (
        "package springfox.documentation" in diagnostic and "does not exist" in diagnostic
    )
    if not (
        missing_configuration
        or missing_annotation
        or missing_handler_providers
        or missing_springfox
    ):
        return ()
    repaired: list[str] = []
    for path in repository.glob("src/main/java/**/*.java"):
        content = path.read_text(errors="replace")
        dedicated_swagger_config = (
            missing_springfox
            and "@Configuration" in content
            and re.search(r"class\s+\w*Swagger\w*Config\w*", content) is not None
            and ("Docket" in content or "transformation of Docket" in content)
        )
        if dedicated_swagger_config:
            path.unlink()
            repaired.append(str(path.relative_to(repository)))
            continue
        updated = content
        if missing_configuration:
            updated = re.sub(
                r"(?m)^import\s+springfox\.documentation\.spring\.data\.rest\."
                r"configuration\.SpringDataRestConfiguration;\s*\n",
                "",
                updated,
            )
            updated = re.sub(
                r"(?m)^[ \t]*@Import\(SpringDataRestConfiguration\.class\)[ \t]*\n",
                "",
                updated,
            )
        if missing_annotation:
            updated = re.sub(
                r"(?m)^import\s+springfox\.documentation\.swagger2\.annotations\."
                r"EnableSwagger2WebMvc;\s*\n",
                "",
                updated,
            )
            updated = re.sub(r"(?m)^[ \t]*@EnableSwagger2WebMvc[ \t]*\n", "", updated)
        if missing_handler_providers:
            updated = re.sub(
                r"(?m)^import\s+springfox\.documentation\.spring\.web\.plugins\."
                r"(?:WebFlux|WebMvc)RequestHandlerProvider;\s*\n",
                "",
                updated,
            )
            method_marker = (
                "public static BeanPostProcessor springfoxHandlerProviderBeanPostProcessor()"
            )
            method_start = updated.find(method_marker)
            if method_start >= 0:
                annotation_start = updated.rfind("@Bean", 0, method_start)
                comment_start = updated.rfind("/**", 0, method_start)
                block_start = max(annotation_start, comment_start)
                opening = updated.find("{", method_start)
                closing = _matching_brace(updated, opening) if opening >= 0 else None
                if closing is not None:
                    updated = updated[:block_start] + updated[closing + 1 :]
            for import_name, identifier in (
                ("org.springframework.beans.BeansException", "BeansException"),
                (
                    "org.springframework.beans.factory.config.BeanPostProcessor",
                    "BeanPostProcessor",
                ),
                ("org.springframework.context.annotation.Bean", "@Bean"),
                ("org.springframework.util.ReflectionUtils", "ReflectionUtils"),
                (
                    "org.springframework.web.servlet.mvc.method.RequestMappingInfoHandlerMapping",
                    "RequestMappingInfoHandlerMapping",
                ),
                ("java.lang.reflect.Field", "Field"),
                ("java.util.List", "List"),
            ):
                source_without_imports = re.sub(r"(?m)^import\s+[^;]+;\s*\n", "", updated)
                if identifier not in source_without_imports:
                    updated = re.sub(
                        rf"(?m)^import\s+{re.escape(import_name)};\s*\n",
                        "",
                        updated,
                    )
        if "@Import(" not in updated:
            updated = re.sub(
                r"(?m)^import\s+org\.springframework\.context\.annotation\.Import;\s*\n",
                "",
                updated,
            )
        if updated != content:
            path.write_text(updated)
            repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


def update_repository_rest_configurer_signature(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Add Boot 2.7's CorsRegistry argument to RepositoryRestConfigurer."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if "method does not override or implement a method from a supertype" not in diagnostic:
        return ()
    method = re.compile(
        r"(?P<prefix>public\s+void\s+configureRepositoryRestConfiguration\s*\(\s*"
        r"RepositoryRestConfiguration\s+[A-Za-z_$][\w$]*\s*)\)"
    )
    repaired: list[str] = []
    for path in repository.glob("src/main/java/**/*.java"):
        content = path.read_text(errors="replace")
        if "implements RepositoryRestConfigurer" not in content:
            continue
        updated, replacements = method.subn(r"\g<prefix>, CorsRegistry cors)", content, count=1)
        if not replacements:
            continue
        cors_import = "import org.springframework.web.servlet.config.annotation.CorsRegistry;"
        if cors_import not in updated:
            imports = tuple(re.finditer(r"(?m)^import\s+[^;]+;\s*$", updated))
            if not imports:
                continue
            insertion = imports[-1].end()
            updated = updated[:insertion] + "\n" + cors_import + updated[insertion:]
        path.write_text(updated)
        repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


def configure_openapi_generator_for_springdoc(
    repository: Path, failure: VerificationResult
) -> bool:
    """Align generated Spring models with Springdoc and the active Boot generation."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    needs_swagger2_annotations = _MISSING_GENERATED_API_MODEL_PROPERTY in diagnostic
    needs_newer_springdoc = (
        _MISSING_SCHEMA_REQUIRED_MODE in diagnostic
        and "io.swagger.v3.oas.annotations.media.Schema" in diagnostic
    )
    needs_boot3_generator = any(marker in diagnostic for marker in _MISSING_GENERATED_JAVAX_APIS)
    if (
        _MISSING_SWAGGER_1_ANNOTATIONS not in diagnostic
        and not needs_swagger2_annotations
        and not needs_newer_springdoc
        and not needs_boot3_generator
    ):
        return False
    pom = repository / "pom.xml"
    if not pom.is_file():
        return False
    content = pom.read_text(errors="replace")
    if "openapi-generator-maven-plugin" not in content:
        return False
    if needs_newer_springdoc:
        springdoc_dependency = re.compile(
            r"(?P<prefix><dependency>\s*"
            r"<groupId>\s*org\.springdoc\s*</groupId>\s*"
            r"<artifactId>\s*springdoc-openapi-ui\s*</artifactId>\s*"
            r"<version>)[^<]+(?P<suffix></version>\s*</dependency>)",
            re.DOTALL,
        )
        updated, replacements = springdoc_dependency.subn(
            r"\g<prefix>1.8.0\g<suffix>", content, count=1
        )
        if not replacements or updated == content:
            return False
        pom.write_text(updated)
        return True
    if needs_swagger2_annotations:
        original_content = content
        content, version_property_replacements = re.subn(
            r"(<openapi-generator-maven-plugin\.version>)[^<]+"
            r"(</openapi-generator-maven-plugin\.version>)",
            r"\g<1>6.6.0\g<2>",
            content,
            count=1,
        )
        plugin_pattern = re.compile(
            r"(?P<plugin><plugin>\s*.*?"
            r"<artifactId>\s*openapi-generator-maven-plugin\s*</artifactId>"
            r".*?</plugin>)",
            re.DOTALL,
        )
        match = plugin_pattern.search(content)
        if match is None:
            return False
        plugin = match.group("plugin")
        if "<annotationLibrary>" in plugin:
            updated_plugin = re.sub(
                r"(<annotationLibrary>)[^<]*(</annotationLibrary>)",
                r"\g<1>swagger2\g<2>",
                plugin,
                count=1,
            )
        else:
            updated_plugin = re.sub(
                r"^(?P<indent>[ \t]*)<configOptions>[ \t]*$",
                lambda option: (
                    f"{option.group('indent')}<configOptions>\n"
                    f"{option.group('indent')}    "
                    "<annotationLibrary>swagger2</annotationLibrary>"
                ),
                plugin,
                count=1,
                flags=re.MULTILINE,
            )
        if not version_property_replacements:
            updated_plugin = re.sub(
                r"(<version>)[^<]+(</version>)",
                r"\g<1>6.6.0\g<2>",
                updated_plugin,
                count=1,
            )
        updated = content[: match.start("plugin")] + updated_plugin + content[match.end("plugin") :]
        if updated == original_content:
            return False
        pom.write_text(updated)
        return True
    if needs_boot3_generator:
        updated = re.sub(
            r"(<openapi-generator-maven-plugin\.version>)[^<]+(</openapi-generator-maven-plugin\.version>)",
            r"\g<1>6.6.0\g<2>",
            content,
            count=1,
        )
        if "<useSpringBoot3>" not in updated:
            updated = re.sub(
                r"^(?P<indent>[ \t]*)<configOptions>[ \t]*$",
                lambda match: (
                    f"{match.group('indent')}<configOptions>\n"
                    f"{match.group('indent')}    <useSpringBoot3>true</useSpringBoot3>"
                ),
                updated,
                count=1,
                flags=re.MULTILINE,
            )
        if updated == content:
            return False
        pom.write_text(updated)
        return True
    if _has_legacy_swagger_annotations(content):
        return False
    dependencies_end = content.find("</dependencies>")
    if dependencies_end < 0:
        return False
    dependency = (
        "        <dependency>\n"
        "            <groupId>io.swagger</groupId>\n"
        "            <artifactId>swagger-annotations</artifactId>\n"
        "            <version>1.6.2</version>\n"
        "        </dependency>\n"
    )
    updated = content[:dependencies_end] + dependency + content[dependencies_end:]
    pom.write_text(updated)
    return True


def repair_springdoc_info_constructor(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Convert OpenRewrite's invalid positional Info constructor to its fluent API."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _INVALID_INFO_CONSTRUCTOR not in diagnostic:
        return ()
    repaired: list[str] = []
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = path.read_text(errors="replace")
        marker = "return new Info("
        start = content.find(marker)
        if start < 0:
            continue
        arguments_start = start + len(marker)
        arguments_end = _matching_parenthesis(content, arguments_start - 1)
        if arguments_end is None:
            continue
        arguments = _split_java_arguments(content[arguments_start:arguments_end])
        if len(arguments) != 8:
            continue
        line_start = content.rfind("\n", 0, start) + 1
        indent = content[line_start:start]
        continuation = indent + "    "
        replacement = (
            "return new Info()\n"
            f"{continuation}.title({arguments[0]})\n"
            f"{continuation}.description({arguments[1]})\n"
            f"{continuation}.version({arguments[2]})\n"
            f"{continuation}.termsOfService({arguments[3]})\n"
            f"{continuation}.contact({arguments[4]})\n"
            f"{continuation}.license(new License().name({arguments[5]}).url({arguments[6]}))"
        )
        updated = content[:start] + replacement + content[arguments_end + 1 :]
        if "io.swagger.v3.oas.models.info.License" not in updated:
            updated = updated.replace(
                "import io.swagger.v3.oas.models.info.Info;",
                "import io.swagger.v3.oas.models.info.Info;\n"
                "import io.swagger.v3.oas.models.info.License;",
                1,
            )
        if "Collections." not in updated:
            updated = re.sub(r"(?m)^import java\.util\.Collections;\s*\n", "", updated)
        path.write_text(updated)
        repaired.append(str(relative))
    return tuple(repaired)


def remove_obsolete_junit_platform_surefire_provider(
    repository: Path, failure: VerificationResult
) -> bool:
    """Remove the retired JUnit Platform provider from a modern Surefire plugin."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if not any(
        marker in diagnostic
        for marker in (_SUREFIRE_TESTSET_FAILURE, _SUREFIRE_CONSOLE_LOGGER_FAILURE)
    ):
        return False
    pom = repository / "pom.xml"
    if not pom.is_file():
        return False
    content = pom.read_text(errors="replace")
    plugin_pattern = re.compile(
        r"(?P<plugin><plugin>.*?<artifactId>maven-surefire-plugin</artifactId>.*?</plugin>)",
        flags=re.DOTALL,
    )
    match = plugin_pattern.search(content)
    if not match or "junit-platform-surefire-provider" not in match.group("plugin"):
        return False
    plugin = match.group("plugin")
    updated_plugin = re.sub(
        r"\s*<dependency>\s*"
        r"<groupId>org\.junit\.platform</groupId>\s*"
        r"<artifactId>junit-platform-surefire-provider</artifactId>"
        r".*?</dependency>",
        "",
        plugin,
        count=1,
        flags=re.DOTALL,
    )
    updated_plugin = re.sub(r"\s*<dependencies>\s*</dependencies>", "", updated_plugin, count=1)
    if updated_plugin == plugin:
        return False
    pom.write_text(content[: match.start()] + updated_plugin + content[match.end() :])
    return True


def configure_boot_validation_starter(repository: Path, failure: VerificationResult) -> bool:
    """Replace a bare validation API with Boot's managed validation implementation."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _MISSING_HIBERNATE_VALIDATOR_CONSTRAINTS not in diagnostic:
        return False
    pom = repository / "pom.xml"
    if not pom.is_file():
        return False
    content = pom.read_text(errors="replace")
    validation_api = re.compile(
        r"(?P<indent>^[ \t]*)<dependency>\s*"
        r"<groupId>javax\.validation</groupId>\s*"
        r"<artifactId>validation-api</artifactId>.*?"
        r"</dependency>[ \t]*(?:\n|$)",
        flags=re.DOTALL | re.MULTILINE,
    )
    match = validation_api.search(content)
    if match is None:
        return False
    if "<artifactId>spring-boot-starter-validation</artifactId>" in content:
        replacement = ""
    else:
        indent = match.group("indent")
        replacement = (
            f"{indent}<dependency>\n"
            f"{indent}    <groupId>org.springframework.boot</groupId>\n"
            f"{indent}    <artifactId>spring-boot-starter-validation</artifactId>\n"
            f"{indent}</dependency>\n"
        )
    pom.write_text(content[: match.start()] + replacement + content[match.end() :])
    return True


def make_password_encoder_bean_independent(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Break Boot 2.6 security configuration cycles around a stateless encoder bean."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _CIRCULAR_SECURITY_CONFIGURATION not in diagnostic or "webSecurityConfig" not in diagnostic:
        return ()
    repaired: list[str] = []
    pattern = re.compile(
        r"(?P<prefix>@Bean[ \t]*\n[ \t]*public[ \t]+)"
        r"(?!static\b)"
        r"(?P<signature>BCryptPasswordEncoder[ \t]+bcryptEncoder[ \t]*\([ \t]*\))"
    )
    for path in repository.glob("src/main/java/**/*.java"):
        content = path.read_text(errors="replace")
        updated, replacements = pattern.subn(r"\g<prefix>static \g<signature>", content, count=1)
        if replacements:
            path.write_text(updated)
            repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


def replace_spring_data_find_one_by_id(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Replace the removed Spring Data ``findOne(id)`` API after a Boot upgrade."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if (
        _SPRING_DATA_FIND_ONE_ID_FAILURE not in diagnostic
        or "cannot be converted to org.springframework.data.domain.Example" not in diagnostic
    ):
        return ()
    failure_locations: dict[str, set[int]] = {}
    for match in re.finditer(
        r"(?m)^(?:\[ERROR\]\s+)?(?P<path>.+\.java):\[(?P<line>\d+),\d+\] "
        r"method findOne in interface",
        diagnostic,
    ):
        failure_locations.setdefault(Path(match.group("path")).name, set()).add(
            int(match.group("line"))
        )
    if not failure_locations:
        return ()
    repaired: list[str] = []
    pattern = re.compile(r"\.findOne\s*\(\s*(?P<id>[A-Za-z_$][\w$]*)\s*\)")
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        affected_lines = failure_locations.get(path.name)
        if not affected_lines:
            continue
        content = path.read_text(errors="replace")
        lines = content.splitlines(keepends=True)
        replacements = 0
        for line_number in affected_lines:
            if line_number > len(lines):
                continue
            lines[line_number - 1], count = pattern.subn(
                r".findById(\g<id>).orElse(null)", lines[line_number - 1]
            )
            replacements += count
        if replacements:
            path.write_text("".join(lines))
            repaired.append(str(relative))
    return tuple(repaired)


def use_explicit_ant_path_request_matchers(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Repair Security 5.8 string matchers when only RequestMatcher[] is available."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if (
        "method requestMatchers" not in diagnostic
        or _REQUEST_MATCHER_SIGNATURE_FAILURE not in diagnostic
    ):
        return ()
    repaired: list[str] = []
    matcher_call = re.compile(r"\.requestMatchers\s*\(")
    string_literal = re.compile(r'"(?:\\.|[^"\\])*"')
    for path in repository.glob("src/main/java/**/*.java"):
        content = path.read_text(errors="replace")
        replacements: list[tuple[int, int, str]] = []
        for match in matcher_call.finditer(content):
            opening = content.find("(", match.start(), match.end())
            closing = _matching_parenthesis(content, opening)
            if closing is None:
                continue
            arguments = _split_java_arguments(content[opening + 1 : closing])
            if not arguments or not all(
                string_literal.fullmatch(argument) for argument in arguments
            ):
                continue
            replacement = ", ".join(
                f"new AntPathRequestMatcher({argument})" for argument in arguments
            )
            replacements.append((opening + 1, closing, replacement))
        if not replacements:
            continue
        updated = content
        for start, end, replacement in reversed(replacements):
            updated = updated[:start] + replacement + updated[end:]
        matcher_import = (
            "import org.springframework.security.web.util.matcher.AntPathRequestMatcher;"
        )
        if matcher_import not in updated:
            imports = tuple(re.finditer(r"(?m)^import\s+[^;]+;\s*$", updated))
            if imports:
                insertion = imports[-1].end()
                updated = updated[:insertion] + "\n" + matcher_import + updated[insertion:]
            else:
                package = re.search(r"(?m)^package\s+[^;]+;\s*$", updated)
                insertion = package.end() if package else 0
                prefix = "\n\n" if insertion else ""
                updated = updated[:insertion] + prefix + matcher_import + "\n" + updated[insertion:]
        path.write_text(updated)
        repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


def remove_legacy_global_method_security_inheritance(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Finish Security 6 annotation migration by removing its legacy superclass."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _LEGACY_METHOD_SECURITY_CONFIGURATION_FAILURE not in diagnostic:
        return ()
    repaired: list[str] = []
    inheritance = re.compile(
        r"(?P<name>\bclass\s+[A-Za-z_$][\w$]*)\s+"
        r"extends\s+GlobalMethodSecurityConfiguration\b"
    )
    legacy_import = re.compile(
        r"(?m)^import\s+org\.springframework\.security\.config\.annotation\.method\."
        r"configuration\.GlobalMethodSecurityConfiguration;\s*\n"
    )
    for path in repository.glob("src/main/java/**/*.java"):
        content = path.read_text(errors="replace")
        if "@EnableMethodSecurity" not in content:
            continue
        updated, replacements = inheritance.subn(r"\g<name>", content, count=1)
        if not replacements:
            continue
        updated = legacy_import.sub("", updated, count=1)
        path.write_text(updated)
        repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


def _matching_parenthesis(content: str, opening: int) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening, len(content)):
        character = content[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _matching_brace(content: str, opening: int) -> int | None:
    """Return the closing brace paired with ``opening``."""

    depth = 0
    for index in range(opening, len(content)):
        if content[index] == "{":
            depth += 1
        elif content[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_java_arguments(content: str) -> list[str]:
    arguments: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(content):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == "," and depth == 0:
            arguments.append(content[start:index].strip())
            start = index + 1
    arguments.append(content[start:].strip())
    return arguments


def configure_boot3_test_compatibility(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Apply narrow compatibility fixes exposed only after a Boot 3 build compiles."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    repairs: list[str] = []
    pom = repository / "pom.xml"
    if _BOOT3_VALIDATION_FAILURE in diagnostic and pom.is_file():
        content = pom.read_text(errors="replace")
        if "openapi-generator-maven-plugin" in content and "<useBeanValidation>" not in content:
            updated = re.sub(
                r"^(?P<indent>[ \t]*)<configOptions>[ \t]*$",
                lambda match: (
                    f"{match.group('indent')}<configOptions>\n"
                    f"{match.group('indent')}    <useBeanValidation>true</useBeanValidation>"
                ),
                content,
                count=1,
                flags=re.MULTILINE,
            )
            if updated != content:
                pom.write_text(updated)
                repairs.append("enabled Jakarta Bean Validation in generated OpenAPI DTOs")
        repairs.extend(_require_nonempty_openapi_fields(repository, diagnostic))

    if _HIBERNATE6_QUERY_FAILURE in diagnostic:
        replacements = {
            "WHERE pet_id=": "WHERE visit.pet.id=",
            "WHERE type_id=": "WHERE pet.type.id=",
        }
        jpa_roots = (repository / "src/main/java",)
        paths = (
            path
            for root in jpa_roots
            for path in root.glob("**/*RepositoryImpl.java")
            if any(part.lower().endswith("jpa") for part in path.relative_to(root).parts[:-1])
        )
        for path in paths:
            content = path.read_text(errors="replace")
            updated = content
            for legacy, entity_path in replacements.items():
                updated = updated.replace(legacy, entity_path)
            if updated != content:
                path.write_text(updated)
                repairs.append(f"updated Hibernate 6 JPQL in {path.relative_to(repository)}")
    if _JDBC_QUERY_FAILURE in diagnostic:
        replacements = {
            "WHERE visit.pet.id=": "WHERE pet_id=",
            "WHERE pet.type.id=": "WHERE type_id=",
        }
        for path in repository.glob("src/main/java/**/jdbc/*RepositoryImpl.java"):
            content = path.read_text(errors="replace")
            updated = content
            for entity_path, column in replacements.items():
                updated = updated.replace(entity_path, column)
            if updated != content:
                path.write_text(updated)
                repairs.append(f"restored JDBC SQL in {path.relative_to(repository)}")
    return tuple(repairs)


def _require_nonempty_openapi_fields(repository: Path, diagnostic: str) -> tuple[str, ...]:
    """Make non-empty request fields explicit when newer generators honor readOnly."""

    controllers = set(re.findall(r"([A-Z][A-Za-z0-9]+)RestControllerTests", diagnostic))
    repairs: list[str] = []
    for path in sorted((repository / "src/main/resources").glob("*.y*ml")):
        content = path.read_text(errors="replace")
        updated = content
        for schema in controllers:
            pattern = re.compile(
                rf"(?ms)^(?P<indent>[ ]+){re.escape(schema)}:\n"
                rf"(?P<body>.*?)(?=^(?P=indent)[A-Za-z0-9_.-]+:\n|\Z)"
            )
            match = pattern.search(updated)
            if match is None:
                continue
            body = match.group("body")
            candidates = re.findall(
                r"(?ms)^ {8}([A-Za-z0-9_.-]+):\n"
                r"(?:(?!^ {8}[A-Za-z0-9_.-]+:).)*?^ {10}minLength: [1-9][0-9]*$",
                body,
            )
            required = set(re.findall(r"(?m)^ {8}- ([A-Za-z0-9_.-]+)$", body))
            missing = [candidate for candidate in candidates if candidate not in required]
            if not missing or "      required:\n" not in body:
                continue
            insertion = "".join(f"        - {candidate}\n" for candidate in missing)
            body = re.sub(
                r"(?m)(^      required:\n(?:        - [^\n]+\n)+)",
                rf"\g<1>{insertion}",
                body,
                count=1,
            )
            updated = updated[: match.start("body")] + body + updated[match.end("body") :]
            repairs.append(f"required non-empty {schema} fields in {path.relative_to(repository)}")
        if updated != content:
            path.write_text(updated)
    return tuple(repairs)


def springdoc_repair_applicable(repository: Path, failure: VerificationResult) -> bool:
    """Return whether a failed build has a known, scoped Springdoc repair."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    pom = repository / "pom.xml"
    generated_annotation_failure = (
        (
            _MISSING_SWAGGER_1_ANNOTATIONS in diagnostic
            or _MISSING_GENERATED_API_MODEL_PROPERTY in diagnostic
            or _MISSING_SCHEMA_REQUIRED_MODE in diagnostic
            or any(marker in diagnostic for marker in _MISSING_GENERATED_JAVAX_APIS)
        )
        and pom.is_file()
        and "openapi-generator-maven-plugin" in pom.read_text(errors="replace")
        and (
            any(marker in diagnostic for marker in _MISSING_GENERATED_JAVAX_APIS)
            or _MISSING_GENERATED_API_MODEL_PROPERTY in diagnostic
            or _MISSING_SCHEMA_REQUIRED_MODE in diagnostic
            or not _has_legacy_swagger_annotations(pom.read_text(errors="replace"))
        )
    )
    boot3_test_failure = (
        _BOOT3_VALIDATION_FAILURE in diagnostic
        or _HIBERNATE6_QUERY_FAILURE in diagnostic
        or _JDBC_QUERY_FAILURE in diagnostic
    )
    invalid_info_constructor = _INVALID_INFO_CONSTRUCTOR in diagnostic and any(
        "return new Info(" in path.read_text(errors="replace")
        for path in repository.rglob("*.java")
        if ".refinery" not in path.relative_to(repository).parts
        and "target" not in path.relative_to(repository).parts
    )
    obsolete_surefire_provider = (
        any(
            marker in diagnostic
            for marker in (_SUREFIRE_TESTSET_FAILURE, _SUREFIRE_CONSOLE_LOGGER_FAILURE)
        )
        and pom.is_file()
        and "junit-platform-surefire-provider" in pom.read_text(errors="replace")
    )
    missing_validation_implementation = (
        _MISSING_HIBERNATE_VALIDATOR_CONSTRAINTS in diagnostic
        and pom.is_file()
        and "<artifactId>validation-api</artifactId>" in pom.read_text(errors="replace")
    )
    circular_security_configuration = (
        _CIRCULAR_SECURITY_CONFIGURATION in diagnostic
        and "webSecurityConfig" in diagnostic
        and any(
            "BCryptPasswordEncoder bcryptEncoder()" in path.read_text(errors="replace")
            for path in repository.glob("src/main/java/**/*.java")
        )
    )
    request_matcher_signature_failure = (
        "method requestMatchers" in diagnostic
        and _REQUEST_MATCHER_SIGNATURE_FAILURE in diagnostic
        and any(
            ".requestMatchers(" in path.read_text(errors="replace")
            for path in repository.glob("src/main/java/**/*.java")
        )
    )
    legacy_method_security_configuration = (
        _LEGACY_METHOD_SECURITY_CONFIGURATION_FAILURE in diagnostic
        and any(
            "@EnableMethodSecurity" in (content := path.read_text(errors="replace"))
            and "extends GlobalMethodSecurityConfiguration" in content
            for path in repository.glob("src/main/java/**/*.java")
        )
    )
    repository_rest_signature_failure = (
        "method does not override or implement a method from a supertype" in diagnostic
        and any(
            "implements RepositoryRestConfigurer" in (content := path.read_text(errors="replace"))
            and "configureRepositoryRestConfiguration(RepositoryRestConfiguration" in content
            for path in repository.glob("src/main/java/**/*.java")
        )
    )
    spring_data_find_one_id_failure = (
        _SPRING_DATA_FIND_ONE_ID_FAILURE in diagnostic
        and "cannot be converted to org.springframework.data.domain.Example" in diagnostic
    )
    return (
        bool(springdoc_repair_files(repository))
        or generated_annotation_failure
        or boot3_test_failure
        or invalid_info_constructor
        or obsolete_surefire_provider
        or missing_validation_implementation
        or circular_security_configuration
        or request_matcher_signature_failure
        or legacy_method_security_configuration
        or repository_rest_signature_failure
        or spring_data_find_one_id_failure
        or (
            _PRIVATE_BEAN_METHOD_FAILURE in diagnostic
            and "must not be private or final" in diagnostic
        )
        or _H2_MISSING_COMMENT_ID in diagnostic
        or _BROKEN_FASTJSON2_CACHE_CONFIG in diagnostic
    )


class DeterministicMigrationRepairer:
    """Apply known, scoped migration repairs without invoking a model."""

    def repair(
        self,
        repository: Path,
        step: RecipeRecommendation,
        failure: VerificationResult,
    ) -> VerificationResult:
        info_repairs = repair_springdoc_info_constructor(repository, failure)
        surefire_repaired = remove_obsolete_junit_platform_surefire_provider(repository, failure)
        generator_repaired = configure_openapi_generator_for_springdoc(repository, failure)
        boot3_repairs = configure_boot3_test_compatibility(repository, failure)
        validation_repaired = bool(
            _BOOT_2_UPGRADE_RECIPE.fullmatch(step.recipe)
        ) and configure_boot_validation_starter(repository, failure)
        encoder_repairs = (
            make_password_encoder_bean_independent(repository, failure)
            if step.recipe == _BOOT_26_RECIPE
            else ()
        )
        request_matcher_repairs = (
            use_explicit_ant_path_request_matchers(repository, failure)
            if step.recipe == _SPRING_SECURITY_58_RECIPE
            else ()
        )
        method_security_repairs = (
            remove_legacy_global_method_security_inheritance(repository, failure)
            if step.recipe == _BOOT_30_RECIPE
            else ()
        )
        springfox_repairs = (
            remove_obsolete_springfox_configuration(repository, failure)
            if step.recipe == _SPRINGFOX_TO_SPRINGDOC_RECIPE
            else ()
        )
        repository_rest_repairs = (
            update_repository_rest_configurer_signature(repository, failure)
            if step.recipe == _BOOT_27_RECIPE
            else ()
        )
        spring_data_find_one_repairs = replace_spring_data_find_one_by_id(repository, failure)
        bean_method_repairs = make_bean_methods_non_private(repository, failure)
        h2_seed_repairs = add_missing_h2_seed_ids(repository, failure)
        fastjson_repairs = restore_fastjson1_cache_configuration(repository, failure)
        if not (
            info_repairs
            or surefire_repaired
            or generator_repaired
            or boot3_repairs
            or validation_repaired
            or encoder_repairs
            or request_matcher_repairs
            or method_security_repairs
            or springfox_repairs
            or repository_rest_repairs
            or spring_data_find_one_repairs
            or bean_method_repairs
            or h2_seed_repairs
            or fastjson_repairs
        ):
            return _result(1, "No deterministic migration repair matched the failure")
        if validation_repaired:
            return _result(
                0,
                stdout=(
                    "Replaced the bare Bean Validation API with Spring Boot's managed "
                    "validation starter"
                ),
            )
        if encoder_repairs:
            return _result(
                0,
                stdout=(
                    "Made the BCrypt password-encoder bean independent of its security "
                    f"configuration: {', '.join(encoder_repairs)}"
                ),
            )
        if request_matcher_repairs:
            return _result(
                0,
                stdout=(
                    "Replaced incompatible Spring Security string request matchers with "
                    "explicit AntPathRequestMatcher instances: "
                    f"{', '.join(request_matcher_repairs)}"
                ),
            )
        if method_security_repairs:
            return _result(
                0,
                stdout=(
                    "Removed legacy GlobalMethodSecurityConfiguration inheritance from "
                    "Spring Security 6 configuration: "
                    f"{', '.join(method_security_repairs)}"
                ),
            )
        if springfox_repairs:
            return _result(
                0,
                stdout=(
                    "Removed obsolete Springfox annotations and imports after the Springdoc "
                    f"conversion: {', '.join(springfox_repairs)}"
                ),
            )
        if repository_rest_repairs:
            return _result(
                0,
                stdout=(
                    "Updated RepositoryRestConfigurer for the Spring Boot 2.7 CorsRegistry "
                    f"signature: {', '.join(repository_rest_repairs)}"
                ),
            )
        if spring_data_find_one_repairs:
            return _result(
                0,
                stdout=(
                    "Replaced removed Spring Data findOne(id) calls with findById(id): "
                    f"{', '.join(spring_data_find_one_repairs)}"
                ),
            )
        if bean_method_repairs:
            return _result(
                0,
                stdout=(f"Made Spring @Bean methods non-private: {', '.join(bean_method_repairs)}"),
            )
        if h2_seed_repairs:
            return _result(0, stdout=f"Added explicit H2 seed IDs: {', '.join(h2_seed_repairs)}")
        if fastjson_repairs:
            return _result(
                0,
                stdout=(
                    "Restored compatible Fastjson Redis configuration: "
                    f"{', '.join(fastjson_repairs)}"
                ),
            )
        details = "; ".join((*info_repairs, *boot3_repairs))
        return _result(
            0,
            stdout=(
                "Updated OpenAPI Generator compatibility and migrated OpenAPI models for the "
                "documentation and framework stack" + (f"; {details}" if details else "")
            ),
        )


class AiMigrationRepairer:
    """Ask a configured model for validated, scoped source replacements."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.repair_mode is RepairMode.LOCAL:
            _require_local_endpoint(settings.litellm_api_base)

    def repair(
        self,
        repository: Path,
        step: RecipeRecommendation,
        failure: VerificationResult,
    ) -> VerificationResult:
        info_repairs = repair_springdoc_info_constructor(repository, failure)
        surefire_repaired = remove_obsolete_junit_platform_surefire_provider(repository, failure)
        generator_repaired = configure_openapi_generator_for_springdoc(repository, failure)
        boot3_repairs = configure_boot3_test_compatibility(repository, failure)
        validation_repaired = bool(
            _BOOT_2_UPGRADE_RECIPE.fullmatch(step.recipe)
        ) and configure_boot_validation_starter(repository, failure)
        encoder_repairs = (
            make_password_encoder_bean_independent(repository, failure)
            if step.recipe == _BOOT_26_RECIPE
            else ()
        )
        bean_method_repairs = make_bean_methods_non_private(repository, failure)
        h2_seed_repairs = add_missing_h2_seed_ids(repository, failure)
        fastjson_repairs = restore_fastjson1_cache_configuration(repository, failure)
        allowed = springdoc_repair_files(repository)
        if not allowed:
            if validation_repaired:
                return _result(
                    0,
                    stdout=(
                        "Replaced the bare Bean Validation API with Spring Boot's managed "
                        "validation starter"
                    ),
                )
            if encoder_repairs:
                return _result(
                    0,
                    stdout=(
                        "Made the BCrypt password-encoder bean independent of its security "
                        f"configuration: {', '.join(encoder_repairs)}"
                    ),
                )
            if bean_method_repairs:
                return _result(
                    0,
                    stdout=(
                        f"Made Spring @Bean methods non-private: {', '.join(bean_method_repairs)}"
                    ),
                )
            if h2_seed_repairs:
                return _result(
                    0, stdout=f"Added explicit H2 seed IDs: {', '.join(h2_seed_repairs)}"
                )
            if fastjson_repairs:
                return _result(
                    0,
                    stdout=(
                        "Restored compatible Fastjson Redis configuration: "
                        f"{', '.join(fastjson_repairs)}"
                    ),
                )
            if info_repairs or surefire_repaired or generator_repaired or boot3_repairs:
                details = "; ".join((*info_repairs, *boot3_repairs))
                return _result(
                    0,
                    stdout=(
                        "Updated OpenAPI Generator compatibility and migrated OpenAPI models "
                        "for the documentation and framework stack"
                        + (f"; {details}" if details else "")
                    ),
                )
            return _result(1, "No residual Springfox source files were found")
        context = []
        for relative in allowed:
            content = (repository / relative).read_text(errors="replace")
            context.append(f"--- {relative}\n{content[: self.settings.max_output_characters]}")
        pom = repository / "pom.xml"
        if pom.is_file():
            context.append(f"--- pom.xml\n{pom.read_text(errors='replace')[:10000]}")
        prompt = (
            "Return only JSON matching this schema: "
            f"{json.dumps(RepairEditProposal.model_json_schema())}\n"
            "Return complete replacement content for only the files that must change to finish "
            "the Springfox Docket to "
            "Springdoc conversion. Preserve API grouping and authorization behavior. Do not "
            "disable checks or edit files outside Allowed files.\n"
            f"Recipe: {step.recipe}\nAllowed files: {[str(path) for path in allowed]}\n"
            f"Build failure:\n{failure.model_dump_json()}\nSources:\n" + "\n".join(context)
        )
        try:
            response = run_tool_loop(
                [
                    {
                        "role": "system",
                        "prompt": "You repair Java migrations with minimal patches.",
                    },
                    {"role": "user", "prompt": prompt},
                ],
                max_turns=1,
                settings=self.settings,
            )
            proposal = TypeAdapter(RepairEditProposal).validate_json(
                _json_payload(response.content or "")
            )
        except Exception as error:
            return _result(1, f"Repair model failed or returned invalid JSON: {error}")
        allowed_names = {str(path) for path in allowed}
        changed = [replacement.path for replacement in proposal.files]
        if len(changed) != len(set(changed)) or set(changed) - allowed_names:
            return _result(1, "Repair proposal attempted files outside the allowed scope")
        for replacement in proposal.files:
            target = (repository / replacement.path).resolve()
            if not target.is_relative_to(repository.resolve()) or not target.is_file():
                return _result(1, f"Unsafe or missing repair target: {replacement.path}")
        for replacement in proposal.files:
            (repository / replacement.path).write_text(replacement.content)
        summary = proposal.summary
        if generator_repaired:
            summary = "Preserved generated DTO annotation compatibility. " + summary
        return _result(0, stdout=summary)


def _require_local_endpoint(api_base: str | None) -> None:
    if not api_base:
        raise ValueError("Local repair mode requires REFINERY_LITELLM_API_BASE")
    host = urlparse(api_base).hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Local repair mode requires a loopback model endpoint")


def _json_payload(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        return stripped[7:-3].strip()
    return stripped


def _result(exit_code: int, stderr: str = "", stdout: str = "") -> VerificationResult:
    return VerificationResult(
        command=["refinery", "ai-repair"],
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0,
    )
