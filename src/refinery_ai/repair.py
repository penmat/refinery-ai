"""Repair-loop guardrails."""

from __future__ import annotations

import hashlib

from refinery_ai.models import VerificationResult


class RepairBudget:
    """Stop repeated or excessive repair attempts."""

    def __init__(self, maximum_attempts: int) -> None:
        if maximum_attempts < 0:
            raise ValueError("maximum_attempts must be non-negative")
        self.maximum_attempts = maximum_attempts
        self.attempts = 0
        self._failures: set[str] = set()

    def allow(self, failure: VerificationResult) -> bool:
        signature = hashlib.sha256(
            f"{failure.exit_code}\0{failure.stderr}\0{failure.stdout}".encode()
        ).hexdigest()
        if self.attempts >= self.maximum_attempts or signature in self._failures:
            return False
        self.attempts += 1
        self._failures.add(signature)
        return True
