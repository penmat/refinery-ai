"""Transactional adapter for an installed, configured pi coding agent."""

from __future__ import annotations

import contextlib
import os
import selectors
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from refinery_ai.agents.repair_snapshot import RepairSnapshot
from refinery_ai.config import Settings
from refinery_ai.migrations.repair import repair_result
from refinery_ai.models import RecipeRecommendation, VerificationResult


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop descendants even if their parent has already exited."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=0.5)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _invoke(
    command: list[str], repository: Path, prompt: str, timeout: int, limit: int
) -> VerificationResult:
    started = time.monotonic()
    buffers = {"stdout": b"", "stderr": b""}
    timed_out = False
    # A disk input avoids blocking on a full stdin pipe while the child writes output.
    with tempfile.TemporaryFile() as source, selectors.DefaultSelector() as selector:
        source.write(prompt.encode())
        source.seek(0)
        process = subprocess.Popen(
            command,
            cwd=repository,
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map() or process.poll() is None:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif limit > 0:
                        name = key.data
                        buffers[name] = (buffers[name] + chunk)[-limit * 4 :]
        finally:
            _stop_process_group(process)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
    stdout = buffers["stdout"].decode("utf-8", errors="replace")[-limit:] if limit > 0 else ""
    stderr = buffers["stderr"].decode("utf-8", errors="replace")[-limit:] if limit > 0 else ""
    if timed_out:
        stderr = f"pi repair timed out after {timeout} seconds.\n{stderr}"
    elif process.returncode:
        stderr = f"pi exited with exit code {process.returncode}.\n{stderr}"
    return VerificationResult(
        command=command,
        exit_code=1 if timed_out else process.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=time.monotonic() - started,
    )


class PiRepairAgent:
    """Run one pi process per candidate; Refinery separately verifies its changes."""

    def __init__(self, settings: Settings) -> None:
        if os.name != "posix":
            raise ValueError("pi repair currently requires POSIX process-group support")
        self.settings = settings
        self._snapshot: RepairSnapshot | None = None

    def repair(
        self, repository: Path, step: RecipeRecommendation, failure: VerificationResult
    ) -> VerificationResult:
        if self._snapshot is not None:
            return repair_result(1, "Previous pi repair must be accepted or rolled back first")
        command = [self.settings.pi_executable, "--print", "--no-session"]
        for flag, value in (
            ("--provider", self.settings.coding_agent_provider),
            ("--model", self.settings.coding_agent_model),
        ):
            if value is not None:
                command.extend([flag, value])
        prompt = (
            "Repair the failed Java migration in this repository. Inspect any relevant files "
            "and run build commands as needed. Preserve application behavior, validation and "
            "authorization. Do not weaken checks, remove tests, or disable validation. "
            "Do not commit, stage, change branches, or publish. Change source/configuration "
            "directly rather than adding build hooks that mutate source. "
            "Make the smallest effective repair and finish with a concise summary.\n"
            f"Project root: {repository.resolve()}\nRecipe: {step.model_dump_json()}\n"
            f"Build failure: {failure.model_dump_json()}\n"
        )
        try:
            self._snapshot = RepairSnapshot.capture(repository)
            result = _invoke(
                command,
                repository,
                prompt,
                self.settings.command_timeout_seconds,
                self.settings.max_output_characters,
            )
            if self._snapshot.git_state_changed():
                result = result.model_copy(
                    update={
                        "exit_code": 1,
                        "stderr": "pi changed Git HEAD or index; repair rejected",
                    }
                )
            elif result.green and not self._snapshot.changed():
                result = result.model_copy(
                    update={
                        "exit_code": 1,
                        "stderr": "pi did not change any source files",
                    }
                )
            if not result.green:
                self.rollback_last()
            return result
        except OSError as error:
            self.rollback_last()
            return repair_result(
                1, f"Could not run pi; ensure it is installed and configured: {error}"
            )
        except Exception as error:
            self.rollback_last()
            return repair_result(1, f"pi repair failed: {error}")
        except BaseException:
            self.rollback_last()
            raise

    def accept_last(self) -> None:
        if self._snapshot is not None:
            self._snapshot.close()
            self._snapshot = None

    def rollback_last(self) -> None:
        if self._snapshot is not None:
            self._snapshot.restore()
            self.accept_last()
