"""Discover installed JDKs and create isolated command environments."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class JdkInstallation(BaseModel):
    major: int
    version: str
    home: Path
    provider: str

    def environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["JAVA_HOME"] = str(self.home)
        entries = environment.get("PATH", "").split(os.pathsep)
        entries = [entry for entry in entries if Path(entry) != self.home / "bin"]
        environment["PATH"] = os.pathsep.join([str(self.home / "bin"), *entries])
        return environment


class JdkProvider(Protocol):
    def installations(self) -> list[JdkInstallation]: ...


class EnvironmentJdkProvider:
    def installations(self) -> list[JdkInstallation]:
        home_value = os.environ.get("JAVA_HOME")
        home = Path(home_value).expanduser() if home_value else None
        if home is None:
            executable = shutil.which("java")
            if executable:
                resolved = Path(executable).resolve()
                home = resolved.parent.parent
        installation = _installation(home, "environment") if home else None
        return [installation] if installation else []


class SdkmanJdkProvider:
    def __init__(self, root: Path | None = None) -> None:
        configured = os.environ.get("SDKMAN_DIR")
        self.root = root or (
            Path(configured).expanduser() if configured else Path.home() / ".sdkman"
        )

    def installations(self) -> list[JdkInstallation]:
        candidates = self.root / "candidates" / "java"
        if not candidates.is_dir():
            return []
        found: list[JdkInstallation] = []
        seen: set[Path] = set()
        for path in sorted(candidates.iterdir()):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            installation = _installation(resolved, "sdkman")
            if installation:
                found.append(installation)
        return found


class SystemJdkProvider:
    """Discover conventional macOS and Linux JDK installation locations."""

    def installations(self) -> list[JdkInstallation]:
        patterns = (
            Path("/Library/Java/JavaVirtualMachines").glob("*/Contents/Home"),
            (Path.home() / "Library/Java/JavaVirtualMachines").glob("*/Contents/Home"),
            Path("/usr/lib/jvm").glob("*"),
        )
        found: list[JdkInstallation] = []
        seen: set[Path] = set()
        for paths in patterns:
            for path in paths:
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                installation = _installation(resolved, "system")
                if installation:
                    found.append(installation)
        return found


class JdkResolver:
    def __init__(self, providers: tuple[JdkProvider, ...] | None = None) -> None:
        self.providers = providers or (
            EnvironmentJdkProvider(),
            SdkmanJdkProvider(),
            SystemJdkProvider(),
        )

    def find(self, major: int) -> JdkInstallation:
        matches = [
            installation
            for provider in self.providers
            for installation in provider.installations()
            if installation.major == major
        ]
        if not matches:
            raise RuntimeError(
                f"No installed JDK {major} was found in JAVA_HOME, PATH, or SDKMAN. "
                f"Install one with SDKMAN, then confirm it with 'sdk list java'."
            )
        return max(matches, key=lambda installation: _version_key(installation.version))

    def environment(self, major: int | None) -> dict[str, str] | None:
        return self.find(major).environment() if major is not None else None


def java_major(value: str | None) -> int | None:
    if not value:
        return None
    match = re.match(r"(?:1\.)?(\d+)", value.strip())
    return int(match.group(1)) if match else None


def project_runtime_java(value: str | None) -> int | None:
    """Choose the conservative bridge JDK used to build the current source target."""

    major = java_major(value)
    return 11 if major is not None and major < 11 else major


def baseline_java_candidates(source: str | None, recipes: list[str]) -> tuple[int, ...]:
    """Try the first Java target, then walk backward through supported LTS bridges."""

    source_major = java_major(source)
    first_target = next(
        (target for recipe in recipes if (target := recipe_target_java(recipe)) is not None),
        None,
    )
    target = first_target or source_major
    if target is None:
        return ()
    lower_bound = source_major or target
    lts_bridges = (25, 21, 17, 11, 8)
    candidates = [target]
    candidates.extend(
        major for major in lts_bridges if lower_bound <= major < target
    )
    if source_major is not None:
        candidates.append(source_major)
    return tuple(dict.fromkeys(candidates))


def recipe_target_java(recipe: str) -> int | None:
    name = recipe.rsplit(".", maxsplit=1)[-1]
    # OpenRewrite's Boot 3 composite recipes include UpgradeToJava17. Treat that
    # implicit target exactly like an explicit Java migration so the migrated
    # source is not verified with the older baseline JDK.
    if re.fullmatch(r"UpgradeSpringBoot_3(?:_\d+)?", name):
        return 17
    match = re.search(r"(?:to|To|UpgradeTo)Java(\d+)$", name)
    return int(match.group(1)) if match else None


@contextmanager
def selected_jdk(environment: dict[str, str] | None) -> Iterator[None]:
    """Temporarily select a JDK for in-process orchestration that launches subprocesses."""

    if environment is None:
        yield
        return
    previous = dict(os.environ)
    os.environ.clear()
    os.environ.update(environment)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def _installation(home: Path, provider: str) -> JdkInstallation | None:
    release = home / "release"
    if not release.is_file() or not (home / "bin" / "java").is_file():
        return None
    try:
        content = release.read_text(errors="replace")
    except OSError:
        return None
    match = re.search(r'^JAVA_VERSION="([^"]+)"', content, flags=re.MULTILINE)
    if not match:
        return None
    version = match.group(1)
    major = java_major(version)
    if major is None:
        return None
    return JdkInstallation(major=major, version=version, home=home, provider=provider)


def _version_key(value: str) -> tuple[int, ...]:
    numbers = tuple(int(number) for number in re.findall(r"\d+", value))
    return numbers or (0,)
