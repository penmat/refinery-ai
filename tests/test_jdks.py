import os
from pathlib import Path

from refinery_ai.jdks import (
    JdkResolver,
    SdkmanJdkProvider,
    baseline_java_candidates,
    java_major,
    project_runtime_java,
    recipe_target_java,
    selected_jdk,
)


def make_jdk(root: Path, name: str, version: str) -> Path:
    home = root / "candidates" / "java" / name
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "java").write_text("")
    (home / "release").write_text(f'JAVA_VERSION="{version}"\n')
    return home


def test_sdkman_provider_finds_installed_jdks_and_resolver_selects_newest(
    tmp_path: Path,
) -> None:
    make_jdk(tmp_path, "11-old", "11.0.20")
    newest = make_jdk(tmp_path, "11-new", "11.0.28")
    make_jdk(tmp_path, "17", "17.0.16")
    provider = SdkmanJdkProvider(tmp_path)

    selected = JdkResolver((provider,)).find(11)

    assert selected.home == newest
    assert selected.major == 11
    assert selected.provider == "sdkman"


def test_selected_jdk_changes_and_restores_process_environment(tmp_path: Path) -> None:
    home = make_jdk(tmp_path, "17", "17.0.16")
    environment = JdkResolver((SdkmanJdkProvider(tmp_path),)).environment(17)
    original_home = os.environ.get("JAVA_HOME")

    with selected_jdk(environment):
        assert os.environ["JAVA_HOME"] == str(home)
        assert os.environ["PATH"].split(os.pathsep)[0] == str(home / "bin")

    assert os.environ.get("JAVA_HOME") == original_home


def test_java_major_and_recipe_targets() -> None:
    assert java_major("1.8") == 8
    assert java_major("11") == 11
    assert project_runtime_java("1.8") == 11
    assert project_runtime_java("17") == 17
    assert recipe_target_java("org.openrewrite.java.migrate.UpgradeToJava17") == 17
    assert recipe_target_java("example.Java11ToJava17") == 17
    assert recipe_target_java("example.UpgradeSpringBoot_3_0") == 17
    assert recipe_target_java("example.UpgradeSpringBoot_3_2") == 17
    assert recipe_target_java("example.UpgradeSpringBoot_2_7") is None
    assert baseline_java_candidates(
        "1.8", ["example.UpgradeSpringBoot", "example.Java8ToJava11"]
    ) == (11, 8)
    assert baseline_java_candidates("1.8", ["example.UpgradeToJava17"]) == (17, 11, 8)
    assert baseline_java_candidates(
        "1.8", ["example.UpgradeSpringBoot_3_0"]
    ) == (17, 11, 8)
    assert baseline_java_candidates("11", ["example.UpgradeToJava21"]) == (21, 17, 11)
