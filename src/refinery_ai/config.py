"""Application configuration."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from refinery_ai.models import RepairMode


class Settings(BaseSettings):
    """Runtime policy and provider configuration."""

    model_config = SettingsConfigDict(env_prefix="REFINERY_", env_file=".env", extra="ignore")

    model: str = "openai:gpt-5.6-terra"
    litellm_model: str = "openai/gpt-5.6-terra"
    litellm_api_base: str | None = None
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    state_directory: Path = Path(".refinery")
    max_repair_attempts: int = 3
    repair_mode: RepairMode = RepairMode.DISABLED
    command_timeout_seconds: int = 900
    codegraph_timeout_seconds: int = 900
    allow_dirty_worktree: bool = False
    max_output_characters: int = 20_000
    recipe_catalog_timeout_seconds: int = 15
    recipe_catalog_verify_ssl: bool = True
    dependency_analysis_enabled: bool = True
    dependency_resolution_timeout_seconds: int = 900
    maven_dependency_plugin_version: str = "3.11.0"
    osv_api_url: str = "https://api.osv.dev"
    osv_timeout_seconds: int = 30
    osv_batch_size: int = 1000
    osv_verify_ssl: bool = True
    sonarqube_enabled: bool = True
    sonarqube_image: str = "sonarqube:26.8.0.126808-community"
    sonarqube_image_pull_timeout_seconds: int = 1800
    sonarqube_startup_timeout_seconds: int = 180
    sonarqube_analysis_timeout_seconds: int = 900
    sonarqube_poll_interval_seconds: float = 2.0
    sonarqube_maven_scanner_version: str = "5.5.0.6356"
    sonarqube_gradle_scanner_version: str = "7.2.2.6593"
