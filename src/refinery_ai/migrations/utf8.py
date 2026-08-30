"""Built-in migration for legacy Java properties-file encodings."""

from __future__ import annotations

from pathlib import Path
from time import monotonic

from refinery_ai.models import DiscoverySnapshot, RecipeRecommendation, VerificationResult

UTF8_APPLICATION_PROPERTIES_RECIPE = "refinery.convert-application-properties-to-utf8"


class Utf8PropertiesMigrationRunner:
    """Convert discovered Java properties resources from ISO-8859-1 to UTF-8."""

    def run(
        self,
        snapshot: DiscoverySnapshot,
        step: RecipeRecommendation,
        *,
        apply: bool,
        timeout: int,
    ) -> VerificationResult:
        del step, timeout
        started = monotonic()
        repository = Path(snapshot.repository).resolve()
        converted: list[str] = []
        for relative in snapshot.non_utf8_application_properties:
            path = (repository / relative).resolve()
            if not path.is_relative_to(repository) or not path.is_file():
                return VerificationResult(
                    command=["refinery", "convert-properties-to-utf8"],
                    exit_code=1,
                    stderr=f"Unsafe or missing properties file: {relative}",
                    duration_seconds=monotonic() - started,
                )
            raw = path.read_bytes()
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                content = raw.decode("iso-8859-1")
            else:
                continue
            if apply:
                path.write_text(content, encoding="utf-8")
            converted.append(str(relative))
        action = "Converted" if apply else "Would convert"
        return VerificationResult(
            command=["refinery", "convert-properties-to-utf8"],
            exit_code=0,
            stdout=f"{action}: " + ", ".join(converted),
            duration_seconds=monotonic() - started,
        )
