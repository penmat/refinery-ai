"""Load retained project evidence for recipe planning."""

from __future__ import annotations

from pathlib import Path


def load_planning_artifacts(repository: Path, state_directory: Path, limit: int) -> str:
    """Return bounded retained artifacts, newest first, with their source paths."""

    root = repository / state_directory
    candidates: list[Path] = []
    for relative in ("assessment.json", "assessment.md", "summary.json", "summary.md"):
        path = root / relative
        if path.is_file():
            candidates.append(path)
    for directory in ("assessments", "dependencies"):
        path = root / directory
        if path.is_dir():
            candidates.extend(item for item in path.iterdir() if item.is_file())
    candidates = sorted(set(candidates), key=lambda path: path.stat().st_mtime, reverse=True)

    sections: list[str] = []
    remaining = limit
    for path in candidates:
        if remaining <= 0:
            break
        try:
            content = path.read_text()
        except (OSError, UnicodeError):
            continue
        header = f"Artifact: {path.relative_to(repository)}\n"
        section = header + content
        sections.append(section[:remaining])
        remaining -= len(section)
    return "\n\n---\n\n".join(sections) or "No persisted project artifacts are available."
