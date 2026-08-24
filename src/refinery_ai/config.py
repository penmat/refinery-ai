"""Application configuration."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime policy and provider configuration."""

    model_config = SettingsConfigDict(env_prefix="REFINERY_", env_file=".env", extra="ignore")

    model: str = "openai:gpt-5-mini"
    state_directory: Path = Path(".refinery")
    max_repair_attempts: int = 3
    command_timeout_seconds: int = 900
    graphify_timeout_seconds: int = 900
    allow_dirty_worktree: bool = False
    max_output_characters: int = 20_000
    sonarqube_enabled: bool = True
    sonarqube_image: str = "sonarqube:26.8.0.126808-community"
    sonarqube_image_pull_timeout_seconds: int = 1800
    sonarqube_startup_timeout_seconds: int = 180
    sonarqube_analysis_timeout_seconds: int = 900
    sonarqube_poll_interval_seconds: float = 2.0
    sonarqube_maven_scanner_version: str = "5.5.0.6356"
