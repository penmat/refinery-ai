"""Durable context receipts owned by Refinery rather than an agent framework."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


class ContextReceipt(BaseModel):
    """Stable address and integrity metadata for context moved out of the model window."""

    handle: str
    kind: str
    content_sha256: str
    byte_count: int
    created_at: datetime


class ContextArtifact(ContextReceipt):
    """Persisted full content associated with a context receipt."""

    content: str


class DurableContextStore:
    """Small JSON artifact store outside the assessed repository."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def persist(self, kind: str, content: str) -> ContextReceipt:
        digest = hashlib.sha256(content.encode()).hexdigest()
        handle = f"{kind}-{digest[:16]}.json"
        artifact = ContextArtifact(
            handle=handle,
            kind=kind,
            content_sha256=digest,
            byte_count=len(content.encode()),
            created_at=datetime.now(UTC),
            content=content,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / handle).write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n"
        )
        return ContextReceipt.model_validate(artifact.model_dump())

    def read(self, receipt: ContextReceipt) -> str:
        artifact = ContextArtifact.model_validate_json((self.root / receipt.handle).read_text())
        digest = hashlib.sha256(artifact.content.encode()).hexdigest()
        if digest != receipt.content_sha256 or digest != artifact.content_sha256:
            raise ValueError(f"Context artifact failed integrity validation: {receipt.handle}")
        return artifact.content
