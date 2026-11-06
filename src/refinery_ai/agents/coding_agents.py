"""Command adapters for installed, configured one-shot coding agents."""

from refinery_ai.agents.cli_repair import CliRepairAgent
from refinery_ai.models import CodingAgentPermissions


def opencode_model(provider: str | None, model: str | None) -> str | None:
    if provider is None:
        return model
    if model is None:
        raise ValueError("OpenCode provider override requires a coding agent model")
    if "/" in model:
        if model.split("/", 1)[0] != provider:
            raise ValueError("OpenCode provider conflicts with the qualified model")
        return model
    return f"{provider}/{model}"


class OpenCodeRepairAgent(CliRepairAgent):
    label = "opencode"

    def command(self) -> list[str]:
        command = [self.settings.opencode_executable, "run"]
        if self.settings.coding_agent_permissions is CodingAgentPermissions.MANAGED:
            command.append("--auto")
        model = opencode_model(
            self.settings.coding_agent_provider, self.settings.coding_agent_model
        )
        if model is not None:
            command.extend(["--model", model])
        return command


class CodexRepairAgent(CliRepairAgent):
    label = "codex"

    def command(self) -> list[str]:
        if self.settings.coding_agent_provider is not None:
            raise ValueError(
                "Codex provider must be configured in Codex; omit coding-agent-provider"
            )
        command = [self.settings.codex_executable]
        managed = self.settings.coding_agent_permissions is CodingAgentPermissions.MANAGED
        if managed:
            command.extend(["--ask-for-approval", "never"])
        command.extend(["exec", "--ephemeral"])
        if managed:
            command.extend(["--sandbox", "workspace-write"])
        if self.settings.coding_agent_model is not None:
            command.extend(["--model", self.settings.coding_agent_model])
        return [*command, "-"]


class ClaudeRepairAgent(CliRepairAgent):
    label = "claude"

    def command(self) -> list[str]:
        if self.settings.coding_agent_provider is not None:
            raise ValueError(
                "Claude provider must be configured in Claude; omit coding-agent-provider"
            )
        command = [self.settings.claude_executable, "--print", "--no-session-persistence"]
        if self.settings.coding_agent_permissions is CodingAgentPermissions.MANAGED:
            command.extend(
                ["--permission-mode", "dontAsk", "--allowedTools", "Read,Edit,Write,Bash,Glob,Grep"]
            )
        if self.settings.coding_agent_model is not None:
            command.extend(["--model", self.settings.coding_agent_model])
        return command
