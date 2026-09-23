"""Signature-driven deterministic repairs for migration leftovers."""

from __future__ import annotations

import re
from pathlib import Path

from refinery_ai.migrations.repair import repair_result as _result
from refinery_ai.models import RecipeRecommendation, VerificationResult

_SPRINGFOX_MARKERS = ("springfox.", "transformation of Docket", "Migrate manually")
_MISSING_SWAGGER_1_ANNOTATIONS = "package io.swagger.annotations does not exist"
_MISSING_GENERATED_API_MODEL_PROPERTY = "symbol:   class ApiModelProperty"
_MISSING_SCHEMA_REQUIRED_MODE = "symbol:   method requiredMode()"
_MISSING_GENERATED_JAVAX_APIS = (
    "package javax.validation does not exist",
    "package javax.annotation does not exist",
)
_BOOT3_VALIDATION_FAILURE = "Status expected:<400> but was:<201>"
_BOOT35_TRAILING_SLASH_FAILURE = re.compile(r"Status expected:<(?:200|201|404)> but was:<400>")
_HIBERNATE6_QUERY_FAILURE = "InvalidDataAccessApiUsage"
_JDBC_QUERY_FAILURE = "BadSqlGrammar"
_INVALID_INFO_CONSTRUCTOR = "constructor Info in class io.swagger.v3.oas.models.info.Info"
_SUREFIRE_TESTSET_FAILURE = "org.apache.maven.surefire.api.testset.TestSetFailedException"
_SUREFIRE_CONSOLE_LOGGER_FAILURE = "org.apache.maven.plugin.surefire.log.api.ConsoleLogger"
_MISSING_HIBERNATE_VALIDATOR_CONSTRAINTS = (
    "package org.hibernate.validator.constraints does not exist"
)
_MISSING_HIBERNATE_VALIDATOR_PACKAGE = "package org.hibernate.validator does not exist"
_MISSING_JAVAX_VALIDATION = "package javax.validation does not exist"
_MAVEN_VERSION_FAILURE = "requires Maven version 3.6.3"
_CIRCULAR_SECURITY_CONFIGURATION = "BeanCurrentlyInCreationException"
_REQUEST_MATCHER_SIGNATURE_FAILURE = (
    "java.lang.String cannot be converted to "
    "org.springframework.security.web.util.matcher.RequestMatcher"
)
_LEGACY_METHOD_SECURITY_CONFIGURATION_FAILURE = "EnableGlobalMethodSecurity is required"
_DUPLICATE_GLOBAL_METHOD_SECURITY_FAILURE = (
    "EnableGlobalMethodSecurity is not a repeatable annotation type"
)
_MILTON_JAVAX_FILTER_FAILURE = (
    "io.milton.servlet.MiltonFilter cannot be converted to jakarta.servlet.Filter"
)
_PRIVATE_BEAN_METHOD_FAILURE = "@Bean method"
_H2_MISSING_COMMENT_ID = 'NULL not allowed for column "COMMENT_ID"'
_BROKEN_FASTJSON2_CACHE_CONFIG = "ObjectReaderProvider cannot be converted to"
_SPRING_DATA_FIND_ONE_ID_FAILURE = (
    "method findOne in interface org.springframework.data.repository.query.QueryByExampleExecutor"
)
_REMOVED_BOOT_2_API_FAILURES = (
    "package org.springframework.boot.bind does not exist",
    "package org.springframework.boot.context.web does not exist",
    "symbol:   method setShowBanner(boolean)",
    "symbol:   method showBanner(boolean)",
)
_SPRING_DATA_LIST_QUERY_FAILURE = "Operator SIMPLE_PROPERTY on"
_SPRING_DATA_GET_BY_ID_AMBIGUITY = "reference to getById is ambiguous"
_MOCKITO_BYTE_BUDDY_FAILURE = "net/bytebuddy/utility/GraalImageCode"
_REMOVED_SECURITY_ADAPTER_FAILURE = "class WebSecurityConfigurerAdapter"
_DISALLOWED_CIRCULAR_REFERENCE_FAILURE = "unresolvable circular reference"
_REMOVED_BOOT_TEST_ANNOTATIONS = "symbol: class SpringApplicationConfiguration"
_REMOVED_HATEOAS_API = "package org.springframework.hateoas.mvc does not exist"
_MISSING_SWAGGER_3_ANNOTATIONS = "package io.swagger.v3.oas.annotations.media does not exist"
_INCOMPATIBLE_THYMELEAF_2_DIALECT = "org/thymeleaf/dialect/IExpressionEnhancingDialect"
_LEGACY_HIBERNATE_INTEGRATION_FAILURE = "java.lang.AbstractMethodError"
_REMOVED_HIBERNATE_SESSION_FACTORY_BEAN = "class HibernateJpaSessionFactoryBean"
_REMOVED_HIBERNATE_QUERY_SET_LONG = "symbol:   method setLong(java.lang.String,long)"
_MISSING_AUTHENTICATION_MANAGER_BUILDER = "symbol:   class AuthenticationManagerBuilder"
_BOOT_26_RECIPE = "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_6"
_BOOT_27_RECIPE = "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7"
_BOOT_30_RECIPE = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"
_SPRING_SECURITY_58_RECIPE = "org.openrewrite.java.spring.security5.UpgradeSpringSecurity_5_8"
_SPRINGFOX_TO_SPRINGDOC_RECIPE = "org.openrewrite.java.springdoc.SpringFoxToSpringDoc"
_BOOT_2_UPGRADE_RECIPE = re.compile(
    r"org\.openrewrite\.java\.spring\.boot2\.UpgradeSpringBoot_2_[3-7]"
)
_BOOT_UPGRADE_RECIPE = re.compile(
    r"org\.openrewrite\.java\.spring\.boot[23]\.UpgradeSpringBoot_[23]_\d+"
)


def _has_legacy_swagger_annotations(content: str) -> bool:
    return bool(
        re.search(
            r"<groupId>\s*io\.swagger\s*</groupId>\s*"
            r"<artifactId>\s*swagger-annotations\s*</artifactId>",
            content,
        )
    )


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
    for path in sorted(repository.glob("src/main/java/**/*.java")):
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
    if not any(
        marker in diagnostic
        for marker in (
            _MISSING_HIBERNATE_VALIDATOR_CONSTRAINTS,
            _MISSING_HIBERNATE_VALIDATOR_PACKAGE,
            _MISSING_JAVAX_VALIDATION,
        )
    ):
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
        if (
            not any(
                marker in diagnostic
                for marker in (
                    _MISSING_HIBERNATE_VALIDATOR_PACKAGE,
                    _MISSING_JAVAX_VALIDATION,
                )
            )
            or "<artifactId>spring-boot-starter-validation</artifactId>" in content
        ):
            return False
        dependencies_end = _project_dependencies_end(content)
        if dependencies_end < 0:
            return False
        dependency = (
            "    <dependency>\n"
            "        <groupId>org.springframework.boot</groupId>\n"
            "        <artifactId>spring-boot-starter-validation</artifactId>\n"
            "    </dependency>\n"
        )
        pom.write_text(content[:dependencies_end] + dependency + content[dependencies_end:])
        return True
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


def upgrade_maven_wrapper(repository: Path, failure: VerificationResult) -> bool:
    """Raise an obsolete checked-in Maven distribution to Boot 3's minimum."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _MAVEN_VERSION_FAILURE not in diagnostic:
        return False
    properties = repository / ".mvn" / "wrapper" / "maven-wrapper.properties"
    if not properties.is_file():
        return False
    content = properties.read_text(errors="replace")
    updated = re.sub(
        r"(?<=/apache-maven/)(?:3\.[0-5]\.\d+|3\.6\.[0-2])"
        r"(?=/apache-maven-(?:3\.[0-5]\.\d+|3\.6\.[0-2])-bin\.zip)",
        "3.6.3",
        content,
    )
    updated = re.sub(
        r"(?<=/apache-maven-)(?:3\.[0-5]\.\d+|3\.6\.[0-2])(?=-bin\.zip)",
        "3.6.3",
        updated,
    )
    if updated == content:
        return False
    properties.write_text(updated)
    return True


def align_spring_boot_32_property(
    repository: Path,
    step: RecipeRecommendation,
    failure: VerificationResult,
) -> bool:
    """Finish a Boot 3.2 composite that left a custom Boot property on 2.x."""

    if step.recipe != "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_2":
        return False
    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if "method does not override or implement a method from a supertype" not in diagnostic:
        return False
    repaired = False
    for pom in sorted(repository.rglob("pom.xml")):
        if ".refinery" in pom.relative_to(repository).parts:
            continue
        content = pom.read_text(errors="replace")
        updated = re.sub(
            r"(<spring-boot\.version>\s*)2\.[^<]+(\s*</spring-boot\.version>)",
            r"\g<1>3.2.12\g<2>",
            content,
        )
        if updated != content:
            pom.write_text(updated)
            repaired = True
    return repaired


def _project_dependencies_end(content: str) -> int:
    """Locate the closing tag of the project's direct dependencies section."""

    depth = 0
    direct_dependencies_depth: int | None = None
    tag_pattern = re.compile(r"<(?P<close>/)?(?P<name>[\w.-]+)(?:\s[^<>]*?)?(?P<empty>/)?>")
    for match in tag_pattern.finditer(content):
        name = match.group("name")
        if match.group("close"):
            if name == "dependencies" and direct_dependencies_depth == depth:
                return match.start()
            depth = max(0, depth - 1)
            continue
        if name == "dependencies" and depth == 1:
            direct_dependencies_depth = depth + 1
        if not match.group("empty"):
            depth += 1
    return -1


def remove_legacy_hibernate4_integrations(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Remove unused Hibernate 4 user-type bridges rejected by Hibernate 5."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if (
        _LEGACY_HIBERNATE_INTEGRATION_FAILURE not in diagnostic
        or "org.hibernate.internal.SessionFactoryImpl" not in diagnostic
    ):
        return ()
    pom = repository / "pom.xml"
    if not pom.is_file():
        return ()
    source = "\n".join(
        path.read_text(errors="replace")
        for path in repository.glob("src/**/*.java")
        if path.is_file()
    )
    if "org.jadira." in source or "joda.time.contrib.hibernate" in source:
        return ()
    content = pom.read_text(errors="replace")
    removed: list[str] = []
    for group_id, artifact_id in (
        ("org.jadira.usertype", "usertype.core"),
        ("joda-time", "joda-time-hibernate"),
    ):
        dependency = re.compile(
            r"(?P<indent>^[ \t]*)<dependency>\s*"
            rf"<groupId>{re.escape(group_id)}</groupId>\s*"
            rf"<artifactId>{re.escape(artifact_id)}</artifactId>.*?"
            r"</dependency>[ \t]*(?:\n|$)",
            flags=re.DOTALL | re.MULTILINE,
        )
        content, count = dependency.subn("", content, count=1)
        if count:
            removed.append(f"{group_id}:{artifact_id}")
    if not removed:
        return ()
    pom.write_text(content)
    return tuple(removed)


def migrate_removed_hibernate6_apis(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Replace narrowly identifiable Spring ORM and Hibernate APIs removed by Boot 3."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    missing_factory_bean = _REMOVED_HIBERNATE_SESSION_FACTORY_BEAN in diagnostic
    missing_query_setter = _REMOVED_HIBERNATE_QUERY_SET_LONG in diagnostic
    if not (missing_factory_bean or missing_query_setter):
        return ()
    repaired: list[str] = []
    factory_method = re.compile(
        r"(?P<indent>^[ \t]*)public[ \t]+HibernateJpaSessionFactoryBean[ \t]+"
        r"sessionFactory\(\)[ \t]*\{[ \t]*\n"
        r"[ \t]*return[ \t]+new[ \t]+HibernateJpaSessionFactoryBean\(\);[ \t]*\n"
        r"[ \t]*\}",
        flags=re.MULTILINE,
    )
    for path in sorted(repository.glob("src/main/java/**/*.java")):
        content = path.read_text(errors="replace")
        updated = content
        if missing_factory_bean and "HibernateJpaSessionFactoryBean" in updated:
            updated = updated.replace(
                "import org.springframework.orm.jpa.vendor.HibernateJpaSessionFactoryBean;",
                "import jakarta.persistence.EntityManagerFactory;\n"
                "import org.hibernate.SessionFactory;",
            )
            updated = factory_method.sub(
                lambda match: (
                    f"{match.group('indent')}public SessionFactory sessionFactory("
                    "EntityManagerFactory entityManagerFactory) {\n"
                    f"{match.group('indent')}    return "
                    "entityManagerFactory.unwrap(SessionFactory.class);\n"
                    f"{match.group('indent')}}}"
                ),
                updated,
                count=1,
            )
        if missing_query_setter and ".setLong(" in updated:
            updated = updated.replace(".setLong(", ".setParameter(")
        if updated != content:
            path.write_text(updated)
            repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


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


def replace_removed_boot_2_apis(repository: Path, failure: VerificationResult) -> tuple[str, ...]:
    """Replace small Boot 1 APIs removed during the Boot 2.0 upgrade."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if not any(marker in diagnostic for marker in _REMOVED_BOOT_2_API_FAILURES):
        return ()
    repaired: list[str] = []
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = path.read_text(errors="replace")
        updated = content
        if "package org.springframework.boot.bind does not exist" in diagnostic:
            updated = updated.replace(
                "import org.springframework.boot.bind.RelaxedPropertyResolver;\n", ""
            )
            updated = updated.replace(
                "private RelaxedPropertyResolver propertyResolver;",
                "private Environment propertyResolver;",
            )
            updated = re.sub(
                r"this\.propertyResolver\s*=\s*new RelaxedPropertyResolver\(\s*"
                r"(?P<environment>[A-Za-z_$][\w$]*)\s*,\s*"
                r'"spring\.datasource\."\s*\);',
                r"this.propertyResolver = \g<environment>;",
                updated,
            )
            updated = re.sub(
                r'propertyResolver\.getProperty\("(?!spring\.datasource\.)([^"\n]+)"\)',
                r'propertyResolver.getProperty("spring.datasource.\1")',
                updated,
            )
        if "package org.springframework.boot.context.web does not exist" in diagnostic:
            updated = updated.replace(
                "org.springframework.boot.context.web.SpringBootServletInitializer",
                "org.springframework.boot.web.servlet.support.SpringBootServletInitializer",
            )
        if "symbol:   method setShowBanner(boolean)" in diagnostic:
            updated = updated.replace(
                ".setShowBanner(false)",
                ".setBannerMode(org.springframework.boot.Banner.Mode.OFF)",
            )
        if "symbol:   method showBanner(boolean)" in diagnostic:
            updated = updated.replace(
                ".showBanner(false)",
                ".bannerMode(org.springframework.boot.Banner.Mode.OFF)",
            )
        if updated != content:
            path.write_text(updated)
            repaired.append(str(relative))
    return tuple(repaired)


def allow_null_in_legacy_mockito_string_matchers(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Preserve Mockito 1's null-matching behavior in a reported failing test."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if "java.lang.NullPointerException" not in diagnostic or "Tests run:" not in diagnostic:
        return ()
    failing_classes = {
        match.group("class").rsplit(".", 1)[-1]
        for match in re.finditer(r"\bat (?P<class>[\w.$]+)\.[\w$]+\([^\n]*\.java:\d+\)", diagnostic)
    }
    repaired: list[str] = []
    for path in repository.rglob("*Test.java"):
        relative = path.relative_to(repository)
        if (
            ".refinery" in relative.parts
            or "target" in relative.parts
            or path.stem not in failing_classes
        ):
            continue
        content = path.read_text(errors="replace")
        if "org.mockito" not in content or "anyString()" not in content:
            continue
        path.write_text(content.replace("anyString()", "nullable(String.class)"))
        repaired.append(str(relative))
    return tuple(repaired)


def remove_conflicting_explicit_mockito(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Use Boot's managed Mockito when an explicit version conflicts with Byte Buddy."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _MOCKITO_BYTE_BUDDY_FAILURE not in diagnostic:
        return ()
    dependency = re.compile(
        r"(?P<indent>^[ \t]*)<dependency>\s*"
        r"<groupId>\s*org\.mockito\s*</groupId>\s*"
        r"<artifactId>\s*mockito-(?:all|core)\s*</artifactId>.*?"
        r"</dependency>\s*",
        re.MULTILINE | re.DOTALL,
    )
    repaired: list[str] = []
    for path in repository.rglob("pom.xml"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = path.read_text(errors="replace")
        updated, replacements = dependency.subn("", content)
        if replacements:
            path.write_text(updated)
            repaired.append(str(relative))
    return tuple(repaired)


def migrate_removed_boot_test_annotations(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Replace Boot 1 integration-test annotations with a Boot 2 test."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _REMOVED_BOOT_TEST_ANNOTATIONS not in diagnostic:
        return ()
    repaired: list[str] = []
    for path in repository.rglob("*Test.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = path.read_text(errors="replace")
        if "@SpringApplicationConfiguration" not in content:
            continue
        updated = re.sub(
            r"import org\.springframework\.boot\.test\.(?:IntegrationTest|"
            r"SpringApplicationConfiguration);\n",
            "",
            content,
        )
        imports = tuple(re.finditer(r"(?m)^import\s+[^;]+;\s*$", updated))
        if not imports:
            continue
        spring_boot_test_import = "import org.springframework.boot.test.context.SpringBootTest;"
        if spring_boot_test_import not in updated:
            insertion = imports[-1].end()
            updated = updated[:insertion] + "\n" + spring_boot_test_import + updated[insertion:]
        updated = re.sub(
            r"@SpringApplicationConfiguration\(classes\s*=\s*([^)]*)\)",
            r"@SpringBootTest(classes = \1)",
            updated,
        )
        updated = re.sub(r"(?m)^@IntegrationTest\s*\n", "", updated)
        path.write_text(updated)
        repaired.append(str(relative))
    return tuple(repaired)


def migrate_removed_hateoas_apis(repository: Path, failure: VerificationResult) -> tuple[str, ...]:
    """Apply Spring HATEOAS 1.0 type and package renames."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _REMOVED_HATEOAS_API not in diagnostic or "ResourceSupport" not in diagnostic:
        return ()
    replacements = {
        "org.springframework.hateoas.mvc.ControllerLinkBuilder": (
            "org.springframework.hateoas.server.mvc.WebMvcLinkBuilder"
        ),
        "org.springframework.hateoas.ResourceSupport": (
            "org.springframework.hateoas.RepresentationModel"
        ),
        "ResourceSupport": "RepresentationModel",
    }
    repaired: list[str] = []
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = path.read_text(errors="replace")
        if "ResourceSupport" not in content and "ControllerLinkBuilder" not in content:
            continue
        updated = content
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if updated != content:
            path.write_text(updated)
            repaired.append(str(relative))
    return tuple(repaired)


def add_missing_swagger_3_annotations_dependency(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Add Swagger 3 annotations to modules whose rewritten sources use Schema."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _MISSING_SWAGGER_3_ANNOTATIONS not in diagnostic:
        return ()
    poms: set[Path] = set()
    for source in repository.rglob("*.java"):
        relative = source.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        if "io.swagger.v3.oas.annotations.media.Schema" not in source.read_text(errors="replace"):
            continue
        parent = source.parent
        while parent == repository or repository in parent.parents:
            pom = parent / "pom.xml"
            if pom.is_file():
                poms.add(pom)
                break
            if parent == repository:
                break
            parent = parent.parent
    repaired: list[str] = []
    dependency = (
        "    <dependency>\n"
        "      <groupId>io.swagger.core.v3</groupId>\n"
        "      <artifactId>swagger-annotations</artifactId>\n"
        "      <version>2.1.13</version>\n"
        "    </dependency>\n"
    )
    for pom in sorted(poms):
        content = pom.read_text(errors="replace")
        if "<groupId>io.swagger.core.v3</groupId>" in content:
            continue
        insertion = content.find("</dependencies>")
        if insertion < 0:
            continue
        pom.write_text(content[:insertion] + dependency + content[insertion:])
        repaired.append(str(pom.relative_to(repository)))
    return tuple(repaired)


def remove_incompatible_thymeleaf_2_joda_dialect(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Remove an unused Joda dialect that cannot run on Thymeleaf 3."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _INCOMPATIBLE_THYMELEAF_2_DIALECT not in diagnostic:
        return ()
    repaired: list[str] = []
    dependency = re.compile(
        r"(?P<indent>^[ \t]*)<dependency>\s*"
        r"<groupId>\s*uk\.co\.gcwilliams\s*</groupId>\s*"
        r"<artifactId>\s*jodatime-thymeleaf-dialect\s*</artifactId>.*?"
        r"</dependency>\s*",
        re.MULTILINE | re.DOTALL,
    )
    version_property = re.compile(
        r"(?m)^[ \t]*<jodatime-thymeleaf\.version>[^<]+"
        r"</jodatime-thymeleaf\.version>\s*\n?"
    )
    for pom in repository.rglob("pom.xml"):
        relative = pom.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = pom.read_text(errors="replace")
        updated, dependency_replacements = dependency.subn("", content)
        if not dependency_replacements:
            continue
        updated = version_property.sub("", updated)
        pom.write_text(updated)
        repaired.append(str(relative))
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = path.read_text(errors="replace")
        if "uk.co.gcwilliams.jodatime.thymeleaf.JodaTimeDialect" not in content:
            continue
        updated = content.replace(
            "import uk.co.gcwilliams.jodatime.thymeleaf.JodaTimeDialect;\n", ""
        )
        updated = re.sub(
            r"\n\s*/\*\*\s*\n\s*\*\s*Joda Time to Thymeleaf converter.*?\*/\s*"
            r"@Bean\s+public JodaTimeDialect jodaTimeDialect\(\)\s*\{\s*"
            r"return new JodaTimeDialect\(\);\s*\}\s*",
            "\n",
            updated,
            count=1,
            flags=re.DOTALL,
        )
        if "JodaTimeDialect" in updated:
            continue
        path.write_text(updated)
        repaired.append(str(relative))
    return tuple(repaired)


def migrate_web_security_configurer_adapter(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Finish a simple adapter-based configuration left behind by the Boot 3 recipe."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _REMOVED_SECURITY_ADAPTER_FAILURE not in diagnostic:
        return ()
    repaired: list[str] = []
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = path.read_text(errors="replace")
        if "extends WebSecurityConfigurerAdapter" not in content:
            continue
        updated = content.replace(
            "import org.springframework.security.config.annotation.authentication.builders."
            "AuthenticationManagerBuilder;\n",
            "",
        ).replace(
            "import org.springframework.security.config.annotation.web.configuration."
            "WebSecurityConfigurerAdapter;\n",
            "import org.springframework.security.web.SecurityFilterChain;\n",
        )
        updated = updated.replace(" extends WebSecurityConfigurerAdapter", "")
        updated = re.sub(
            r"\n\s*/\*~~\(Migrate manually.*?\)~~>\*/@Override\s*\n"
            r"\s*protected void configure\(AuthenticationManagerBuilder auth\)"
            r" throws Exception \{.*?\n\s*\}\n",
            "\n",
            updated,
            count=1,
            flags=re.DOTALL,
        )
        security_method = re.compile(
            r"\s*@Override\s*\n\s*protected void configure\(HttpSecurity http\)"
            r" throws Exception \{(?P<body>.*?)\n\s*\}",
            re.DOTALL,
        )

        def replace_security_method(match: re.Match[str]) -> str:
            body = match.group("body")
            body = body.replace(".authorizeRequests()", ".authorizeHttpRequests()")
            body = body.replace(".antMatchers(", ".requestMatchers(")
            return (
                "\n    @Bean\n"
                "    public SecurityFilterChain securityFilterChain(HttpSecurity http) "
                "throws Exception {"
                f"{body}\n"
                "        return http.build();\n"
                "    }"
            )

        updated, replacements = security_method.subn(replace_security_method, updated, count=1)
        if not replacements:
            continue
        path.write_text(updated)
        repaired.append(str(relative))
    return tuple(repaired)


def finish_security_6_authentication_manager(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Finish adapter-era authentication hooks after creating a filter chain."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _MISSING_AUTHENTICATION_MANAGER_BUILDER not in diagnostic:
        return ()
    repaired: list[str] = []
    manual_marker = r"(?:/\*~~\(Migrate manually.*?\)~~>\*/)*"
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = path.read_text(errors="replace")
        if "AuthenticationManagerBuilder" not in content or "SecurityFilterChain" not in content:
            continue
        updated = content.replace(
            "import org.springframework.security.config.BeanIds;\n", ""
        ).replace(
            "import org.springframework.security.config.annotation.authentication.builders."
            "AuthenticationManagerBuilder;\n",
            "",
        )
        updated = re.sub(
            rf"\n\s*{manual_marker}public void configure\(AuthenticationManagerBuilder "
            r"[A-Za-z_$][\w$]*\) throws Exception \{.*?\n\s*\}\n",
            "\n",
            updated,
            count=1,
            flags=re.DOTALL,
        )
        authentication_configuration_import = (
            "import org.springframework.security.config.annotation.authentication."
            "configuration.AuthenticationConfiguration;"
        )
        if authentication_configuration_import not in updated:
            imports = tuple(re.finditer(r"(?m)^import\s+[^;]+;\s*$", updated))
            if not imports:
                continue
            insertion = imports[-1].end()
            updated = (
                updated[:insertion]
                + "\n"
                + authentication_configuration_import
                + updated[insertion:]
            )
        manager_method = re.compile(
            rf"{manual_marker}@Bean(?:\(BeanIds\.AUTHENTICATION_MANAGER\))?\s*\n"
            r"\s*public AuthenticationManager authenticationManagerBean\(\) "
            r"throws Exception \{\s*return super\.authenticationManagerBean\(\);\s*\}",
            re.DOTALL,
        )
        replacement = (
            "@Bean\n"
            "\tpublic AuthenticationManager authenticationManagerBean(\n"
            "\t\t\tAuthenticationConfiguration configuration) throws Exception {\n"
            "\t\treturn configuration.getAuthenticationManager();\n"
            "\t}"
        )
        updated, replacements = manager_method.subn(replacement, updated, count=1)
        if not replacements or "AuthenticationManagerBuilder" in updated:
            continue
        path.write_text(updated)
        repaired.append(str(relative))
    return tuple(repaired)


def allow_circular_references_for_legacy_application(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Retain a legacy application's established bean graph after Boot 2.6."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if (
        "BeanCurrentlyInCreationException" not in diagnostic
        or _DISALLOWED_CIRCULAR_REFERENCE_FAILURE not in diagnostic
    ):
        return ()
    repaired: list[str] = []
    for path in repository.rglob("application.properties"):
        relative = path.relative_to(repository)
        if (
            ".refinery" in relative.parts
            or "target" in relative.parts
            or "resources" not in relative.parts
            or "test" not in relative.parts
        ):
            continue
        content = path.read_text(errors="replace")
        if "spring.main.allow-circular-references=" in content:
            continue
        separator = "" if not content or content.endswith("\n") else "\n"
        path.write_text(f"{content}{separator}spring.main.allow-circular-references=true\n")
        repaired.append(str(relative))
    return tuple(repaired)


def make_spring_data_list_query_explicit(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Use the ``In`` operator for a derived query receiving a List argument."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if (
        _SPRING_DATA_LIST_QUERY_FAILURE not in diagnostic
        or "requires a scalar argument, found interface java.util.List" not in diagnostic
    ):
        return ()
    method_match = re.search(
        r"Failed to create query for method .*?\.(?P<method>findBy[A-Za-z_$][\w$]*)"
        r"\(java\.util\.List,",
        diagnostic,
    )
    if method_match is None:
        return ()
    method = method_match.group("method")
    if method.endswith("In"):
        return ()
    repaired: list[str] = []
    pattern = re.compile(rf"\b{re.escape(method)}\b")
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        if ".refinery" in relative.parts or "target" in relative.parts:
            continue
        content = path.read_text(errors="replace")
        updated, replacements = pattern.subn(f"{method}In", content)
        if replacements:
            path.write_text(updated)
            repaired.append(str(relative))
    return tuple(repaired)


def disambiguate_custom_repository_get_by_id(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Select a pre-existing custom getById method after JpaRepository adds its own."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if (
        _SPRING_DATA_GET_BY_ID_AMBIGUITY not in diagnostic
        or "in org.springframework.data.jpa.repository.JpaRepository match" not in diagnostic
    ):
        return ()
    custom_match = re.search(
        r"both method getById\([^)]*\) in (?P<custom>[\w.$]+) and method getById",
        diagnostic,
    )
    if custom_match is None:
        return ()
    custom_type = custom_match.group("custom")
    failure_locations: dict[str, set[int]] = {}
    for match in re.finditer(
        r"(?m)^(?:\[ERROR\]\s+)?(?P<path>.+\.java):\[(?P<line>\d+),\d+\] "
        + re.escape(_SPRING_DATA_GET_BY_ID_AMBIGUITY),
        diagnostic,
    ):
        failure_locations.setdefault(Path(match.group("path")).name, set()).add(
            int(match.group("line"))
        )
    repaired: list[str] = []
    call = re.compile(r"(?P<receiver>\b[A-Za-z_$][\w$]*)\.getById\(")
    for path in repository.rglob("*.java"):
        relative = path.relative_to(repository)
        affected_lines = failure_locations.get(path.name)
        if not affected_lines or ".refinery" in relative.parts or "target" in relative.parts:
            continue
        lines = path.read_text(errors="replace").splitlines(keepends=True)
        replacements = 0
        for line_number in affected_lines:
            if line_number > len(lines):
                continue
            lines[line_number - 1], count = call.subn(
                rf"(({custom_type}) \g<receiver>).getById(", lines[line_number - 1]
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
    if not any(
        marker in diagnostic
        for marker in (
            _LEGACY_METHOD_SECURITY_CONFIGURATION_FAILURE,
            _DUPLICATE_GLOBAL_METHOD_SECURITY_FAILURE,
        )
    ):
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
        updated = re.sub(
            r"(?m)^\s*@(?:org\.springframework\.security\.config\.annotation\.method\."
            r"configuration\.)?EnableGlobalMethodSecurity\([^\n]*\)\s*\n",
            "",
            updated,
        )
        updated = re.sub(
            r"(?m)^import\s+org\.springframework\.security\.config\.annotation\.method\."
            r"configuration\.EnableGlobalMethodSecurity;\s*\n",
            "",
            updated,
        )
        method_security = re.search(r"@EnableMethodSecurity(?:\((?P<body>.*?)\))?", updated, re.S)
        if method_security and "prePostEnabled" not in (method_security.group("body") or ""):
            body = (method_security.group("body") or "").strip()
            replacement = "@EnableMethodSecurity(prePostEnabled = true"
            if body:
                replacement += ",\n        " + body
            replacement += ")"
            updated = updated[: method_security.start()] + replacement + updated[method_security.end() :]
        if not replacements and updated == content:
            continue
        updated = legacy_import.sub("", updated, count=1)
        path.write_text(updated)
        repaired.append(str(path.relative_to(repository)))
    return tuple(repaired)


def upgrade_milton_for_jakarta_servlet(
    repository: Path, failure: VerificationResult
) -> tuple[str, ...]:
    """Upgrade Milton's legacy javax Servlet implementation for Spring Boot 3."""

    diagnostic = f"{failure.stdout}\n{failure.stderr}"
    if _MILTON_JAVAX_FILTER_FAILURE not in diagnostic:
        return ()
    repaired: list[str] = []
    for path in repository.rglob("pom.xml"):
        if ".refinery" in path.relative_to(repository).parts:
            continue
        content = path.read_text(errors="replace")
        updated = re.sub(
            r"(<groupId>\s*io\.milton\s*</groupId>\s*"
            r"<artifactId>\s*milton-server-ce\s*</artifactId>\s*"
            r"<version>\s*)[^<]+(\s*</version>)",
            r"\g<1>4.2.0.2712\2",
            content,
            count=1,
        )
        if updated != content:
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
    if _BOOT35_TRAILING_SLASH_FAILURE.search(diagnostic):
        for path in repository.glob("src/main/java/**/*Controller.java"):
            content = path.read_text(errors="replace")
            updated = re.sub(
                r"@(Get|Post|Put|Patch|Delete)Mapping\(value\s*=\s*\"\"",
                r'@\1Mapping(value = {"", "/"}',
                content,
            )
            if updated != content:
                path.write_text(updated)
                repairs.append(
                    f"preserved trailing-slash routes in {path.relative_to(repository)}"
                )
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
        transient_pet_type = (
            "TransientObjectException: persistent instance references an unsaved transient "
            "instance of 'org.springframework.samples.petclinic.model.PetType'"
        )
        if transient_pet_type in diagnostic:
            for path in repository.glob("src/main/java/**/*PetTypeRepositoryImpl.java"):
                content = path.read_text(errors="replace")
                updated = re.sub(
                    r"(?m)^\s*this\.em\.remove\(this\.em\.contains\(petType\) \? petType : "
                    r"this\.em\.merge\(petType\)\);\s*\n",
                    "",
                    content,
                    count=1,
                )
                if updated != content and "this.em.clear();" not in updated:
                    updated = re.sub(
                        r'(this\.em\.createQuery\("DELETE FROM PetType pettype WHERE id="[^\n]+'
                        r"\.executeUpdate\(\);)",
                        r"\1\n        this.em.clear();",
                        updated,
                        count=1,
                    )
                if updated != content:
                    path.write_text(updated)
                    repairs.append(
                        "deferred PetType deletion until after its dependent pets in "
                        f"{path.relative_to(repository)}"
                    )
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
    missing_validation_implementation = pom.is_file() and (
        (
            _MISSING_HIBERNATE_VALIDATOR_CONSTRAINTS in diagnostic
            and "<artifactId>validation-api</artifactId>" in pom.read_text(errors="replace")
        )
        or _MISSING_HIBERNATE_VALIDATOR_PACKAGE in diagnostic
        or _MISSING_JAVAX_VALIDATION in diagnostic
    )
    partial_boot3_property_upgrade = (
        "method does not override or implement a method from a supertype" in diagnostic
        and any(
            re.search(
                r"<spring-boot\.version>\s*2\.",
                candidate.read_text(errors="replace"),
            )
            for candidate in repository.rglob("pom.xml")
            if ".refinery" not in candidate.relative_to(repository).parts
        )
    )
    obsolete_maven_wrapper = _MAVEN_VERSION_FAILURE in diagnostic and (
        repository / ".mvn" / "wrapper" / "maven-wrapper.properties"
    ).is_file()
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
        any(
            marker in diagnostic
            for marker in (
                _LEGACY_METHOD_SECURITY_CONFIGURATION_FAILURE,
                _DUPLICATE_GLOBAL_METHOD_SECURITY_FAILURE,
            )
        )
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
    removed_boot_2_api_failure = any(
        marker in diagnostic for marker in _REMOVED_BOOT_2_API_FAILURES
    )
    spring_data_list_query_failure = (
        _SPRING_DATA_LIST_QUERY_FAILURE in diagnostic
        and "requires a scalar argument, found interface java.util.List" in diagnostic
    )
    spring_data_get_by_id_ambiguity = (
        _SPRING_DATA_GET_BY_ID_AMBIGUITY in diagnostic
        and "in org.springframework.data.jpa.repository.JpaRepository match" in diagnostic
    )
    legacy_mockito_null_matcher_failure = (
        "java.lang.NullPointerException" in diagnostic
        and "Tests run:" in diagnostic
        and any(
            "org.mockito" in (content := path.read_text(errors="replace"))
            and "anyString()" in content
            for path in repository.rglob("*Test.java")
            if ".refinery" not in path.relative_to(repository).parts
            and "target" not in path.relative_to(repository).parts
        )
    )
    conflicting_explicit_mockito_failure = _MOCKITO_BYTE_BUDDY_FAILURE in diagnostic and any(
        re.search(
            r"<artifactId>\s*mockito-(?:all|core)\s*</artifactId>",
            path.read_text(errors="replace"),
        )
        for path in repository.rglob("pom.xml")
        if ".refinery" not in path.relative_to(repository).parts
    )
    removed_security_adapter_failure = _REMOVED_SECURITY_ADAPTER_FAILURE in diagnostic and any(
        "extends WebSecurityConfigurerAdapter" in path.read_text(errors="replace")
        for path in repository.rglob("*.java")
        if ".refinery" not in path.relative_to(repository).parts
        and "target" not in path.relative_to(repository).parts
    )
    disallowed_circular_reference_failure = (
        "BeanCurrentlyInCreationException" in diagnostic
        and _DISALLOWED_CIRCULAR_REFERENCE_FAILURE in diagnostic
    )
    removed_boot_test_annotations = _REMOVED_BOOT_TEST_ANNOTATIONS in diagnostic
    removed_hateoas_api = _REMOVED_HATEOAS_API in diagnostic and "ResourceSupport" in diagnostic
    missing_swagger_3_annotations = _MISSING_SWAGGER_3_ANNOTATIONS in diagnostic
    incompatible_thymeleaf_2_dialect = _INCOMPATIBLE_THYMELEAF_2_DIALECT in diagnostic and any(
        "<artifactId>jodatime-thymeleaf-dialect</artifactId>" in path.read_text(errors="replace")
        for path in repository.rglob("pom.xml")
        if ".refinery" not in path.relative_to(repository).parts
    )
    legacy_hibernate4_integration_failure = (
        _LEGACY_HIBERNATE_INTEGRATION_FAILURE in diagnostic
        and "org.hibernate.internal.SessionFactoryImpl" in diagnostic
        and pom.is_file()
        and any(
            artifact in pom.read_text(errors="replace")
            for artifact in (
                "<artifactId>usertype.core</artifactId>",
                "<artifactId>joda-time-hibernate</artifactId>",
            )
        )
    )
    removed_hibernate6_api_failure = (
        _REMOVED_HIBERNATE_SESSION_FACTORY_BEAN in diagnostic
        or _REMOVED_HIBERNATE_QUERY_SET_LONG in diagnostic
    )
    incomplete_security_6_authentication = (
        _MISSING_AUTHENTICATION_MANAGER_BUILDER in diagnostic
        and any(
            "SecurityFilterChain" in (content := path.read_text(errors="replace"))
            and "AuthenticationManagerBuilder" in content
            for path in repository.rglob("*.java")
            if ".refinery" not in path.relative_to(repository).parts
            and "target" not in path.relative_to(repository).parts
        )
    )
    incompatible_milton_filter = _MILTON_JAVAX_FILTER_FAILURE in diagnostic
    return (
        bool(springdoc_repair_files(repository))
        or generated_annotation_failure
        or boot3_test_failure
        or invalid_info_constructor
        or obsolete_surefire_provider
        or missing_validation_implementation
        or partial_boot3_property_upgrade
        or obsolete_maven_wrapper
        or circular_security_configuration
        or request_matcher_signature_failure
        or legacy_method_security_configuration
        or repository_rest_signature_failure
        or spring_data_find_one_id_failure
        or removed_boot_2_api_failure
        or spring_data_list_query_failure
        or spring_data_get_by_id_ambiguity
        or legacy_mockito_null_matcher_failure
        or conflicting_explicit_mockito_failure
        or removed_security_adapter_failure
        or disallowed_circular_reference_failure
        or removed_boot_test_annotations
        or removed_hateoas_api
        or missing_swagger_3_annotations
        or incompatible_thymeleaf_2_dialect
        or legacy_hibernate4_integration_failure
        or removed_hibernate6_api_failure
        or incomplete_security_6_authentication
        or incompatible_milton_filter
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
            _BOOT_UPGRADE_RECIPE.fullmatch(step.recipe)
        ) and configure_boot_validation_starter(repository, failure)
        maven_wrapper_repaired = upgrade_maven_wrapper(repository, failure)
        boot_property_repaired = align_spring_boot_32_property(
            repository, step, failure
        )
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
        removed_boot_2_api_repairs = replace_removed_boot_2_apis(repository, failure)
        spring_data_list_query_repairs = make_spring_data_list_query_explicit(repository, failure)
        get_by_id_repairs = disambiguate_custom_repository_get_by_id(repository, failure)
        mockito_repairs = allow_null_in_legacy_mockito_string_matchers(repository, failure)
        mockito_dependency_repairs = remove_conflicting_explicit_mockito(repository, failure)
        boot_test_annotation_repairs = migrate_removed_boot_test_annotations(repository, failure)
        hateoas_repairs = migrate_removed_hateoas_apis(repository, failure)
        swagger_3_dependency_repairs = add_missing_swagger_3_annotations_dependency(
            repository, failure
        )
        thymeleaf_dialect_repairs = remove_incompatible_thymeleaf_2_joda_dialect(
            repository, failure
        )
        hibernate4_integration_repairs = remove_legacy_hibernate4_integrations(repository, failure)
        hibernate6_api_repairs = migrate_removed_hibernate6_apis(repository, failure)
        security_adapter_repairs = (
            migrate_web_security_configurer_adapter(repository, failure)
            if step.recipe == _BOOT_30_RECIPE
            else ()
        )
        security_authentication_repairs = (
            finish_security_6_authentication_manager(repository, failure)
            if step.recipe == _BOOT_30_RECIPE
            else ()
        )
        circular_reference_repairs = (
            allow_circular_references_for_legacy_application(repository, failure)
            if step.recipe == _BOOT_26_RECIPE
            else ()
        )
        bean_method_repairs = make_bean_methods_non_private(repository, failure)
        h2_seed_repairs = add_missing_h2_seed_ids(repository, failure)
        fastjson_repairs = restore_fastjson1_cache_configuration(repository, failure)
        milton_repairs = upgrade_milton_for_jakarta_servlet(repository, failure)
        if not (
            info_repairs
            or surefire_repaired
            or generator_repaired
            or boot3_repairs
            or validation_repaired
            or maven_wrapper_repaired
            or boot_property_repaired
            or encoder_repairs
            or request_matcher_repairs
            or method_security_repairs
            or springfox_repairs
            or repository_rest_repairs
            or spring_data_find_one_repairs
            or removed_boot_2_api_repairs
            or spring_data_list_query_repairs
            or get_by_id_repairs
            or mockito_repairs
            or mockito_dependency_repairs
            or security_adapter_repairs
            or security_authentication_repairs
            or circular_reference_repairs
            or boot_test_annotation_repairs
            or hateoas_repairs
            or swagger_3_dependency_repairs
            or thymeleaf_dialect_repairs
            or hibernate4_integration_repairs
            or hibernate6_api_repairs
            or bean_method_repairs
            or h2_seed_repairs
            or fastjson_repairs
            or milton_repairs
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
        if maven_wrapper_repaired:
            return _result(
                0,
                stdout="Upgraded the checked-in Maven wrapper distribution to Maven 3.6.3",
            )
        if boot_property_repaired:
            return _result(
                0,
                stdout=(
                    "Aligned a custom spring-boot.version property with the Boot 3.2 target"
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
        if milton_repairs:
            return _result(
                0,
                stdout=(
                    "Upgraded Milton to its Jakarta Servlet-compatible release: "
                    f"{', '.join(milton_repairs)}"
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
        if removed_boot_2_api_repairs:
            return _result(
                0,
                stdout=(
                    "Replaced APIs removed by Spring Boot 2.0: "
                    f"{', '.join(removed_boot_2_api_repairs)}"
                ),
            )
        if spring_data_list_query_repairs:
            return _result(
                0,
                stdout=(
                    "Made the Spring Data list-valued derived query operator explicit: "
                    f"{', '.join(spring_data_list_query_repairs)}"
                ),
            )
        if get_by_id_repairs:
            return _result(
                0,
                stdout=(
                    "Disambiguated the custom repository getById method from JpaRepository: "
                    f"{', '.join(get_by_id_repairs)}"
                ),
            )
        if mockito_repairs:
            return _result(
                0,
                stdout=(
                    "Preserved legacy Mockito null string matching in failing tests: "
                    f"{', '.join(mockito_repairs)}"
                ),
            )
        if mockito_dependency_repairs:
            return _result(
                0,
                stdout=(
                    "Removed obsolete mockito-all in favor of Spring Boot's managed Mockito: "
                    f"{', '.join(mockito_dependency_repairs)}"
                ),
            )
        if security_adapter_repairs:
            return _result(
                0,
                stdout=(
                    "Migrated WebSecurityConfigurerAdapter to a SecurityFilterChain bean: "
                    f"{', '.join(security_adapter_repairs)}"
                ),
            )
        if security_authentication_repairs:
            return _result(
                0,
                stdout=(
                    "Migrated adapter-era authentication hooks to AuthenticationConfiguration: "
                    f"{', '.join(security_authentication_repairs)}"
                ),
            )
        if circular_reference_repairs:
            return _result(
                0,
                stdout=(
                    "Enabled circular references for the legacy application bean graph: "
                    f"{', '.join(circular_reference_repairs)}"
                ),
            )
        if boot_test_annotation_repairs:
            return _result(
                0,
                stdout=(
                    "Migrated removed Spring Boot integration-test annotations: "
                    f"{', '.join(boot_test_annotation_repairs)}"
                ),
            )
        if hateoas_repairs:
            return _result(
                0,
                stdout=(
                    "Migrated Spring HATEOAS 1.0 type and package renames: "
                    f"{', '.join(hateoas_repairs)}"
                ),
            )
        if swagger_3_dependency_repairs:
            return _result(
                0,
                stdout=(
                    "Added Swagger 3 annotations to rewritten source modules: "
                    f"{', '.join(swagger_3_dependency_repairs)}"
                ),
            )
        if thymeleaf_dialect_repairs:
            return _result(
                0,
                stdout=(
                    "Removed the unused Thymeleaf 2-only Joda dialect: "
                    f"{', '.join(thymeleaf_dialect_repairs)}"
                ),
            )
        if hibernate4_integration_repairs:
            return _result(
                0,
                stdout=(
                    "Removed unused Hibernate 4 integration libraries incompatible with "
                    f"Hibernate 5: {', '.join(hibernate4_integration_repairs)}"
                ),
            )
        if hibernate6_api_repairs:
            return _result(
                0,
                stdout=(
                    "Migrated Spring ORM and Hibernate APIs removed by Boot 3: "
                    f"{', '.join(hibernate6_api_repairs)}"
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
