"""One-shot adapter for an installed, configured pi coding agent."""

from refinery_ai.agents.cli_repair import CliRepairAgent


class PiRepairAgent(CliRepairAgent):
    label = "pi"

    def command(self) -> list[str]:
        command = [self.settings.pi_executable, "--print", "--no-session"]
        for flag, value in (
            ("--provider", self.settings.coding_agent_provider),
            ("--model", self.settings.coding_agent_model),
        ):
            if value is not None:
                command.extend([flag, value])
        return command
