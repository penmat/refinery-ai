"""Immutable assessment files and friendly-name index."""

from __future__ import annotations

import json
import re
from pathlib import Path

from refinery_ai.assessment.models import Assessment

_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


class AssessmentStore:
    def __init__(self, repository: Path, state_directory: Path = Path(".refinery")) -> None:
        self.root = repository.expanduser().resolve() / state_directory / "assessments"

    def save(self, assessment: Assessment) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{assessment.assessment_id}.json"
        if path.exists():
            raise FileExistsError(f"Assessment already exists: {assessment.assessment_id}")
        self._write_json(path, assessment.model_dump(mode="json"))
        if assessment.name:
            self._validate_name(assessment.name)
            names = self._names()
            names[assessment.name] = assessment.assessment_id
            self._write_json(self.root / "names.json", dict(sorted(names.items())))
        return path

    def load(self, reference: str) -> Assessment:
        assessment_id = self._names().get(reference, reference)
        path = self.root / f"{assessment_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Assessment not found: {reference}")
        return Assessment.model_validate_json(path.read_text())

    def list(self) -> list[Assessment]:
        if not self.root.is_dir():
            return []
        return sorted(
            (
                Assessment.model_validate_json(path.read_text())
                for path in self.root.glob("*.json")
                if path.name != "names.json"
            ),
            key=lambda item: item.created_at,
            reverse=True,
        )

    def _names(self) -> dict[str, str]:
        path = self.root / "names.json"
        if not path.is_file():
            return {}
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise ValueError("Invalid assessment name index")
        return value

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _NAME.fullmatch(name):
            raise ValueError("Assessment names may contain letters, numbers, '.', '_', and '-'")

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)
