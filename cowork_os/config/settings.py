"""Settings module for coworkOS — pydantic-settings based configuration."""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """coworkOS application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── LLM Provider ─────────────────────────────────────────────────────────
    llm_provider: Literal["anthropic", "openai", "ollama"] = "anthropic"
    llm_model: str = "claude-haiku-4-5-20251001"

    # ─── API Keys ─────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # ─── Browser Settings ─────────────────────────────────────────────────────
    browser_headless: bool = True
    browser_profile_dir: str = "./profiles"

    # ─── Agent Settings ───────────────────────────────────────────────────────
    max_steps: int = 50
    token_budget: int = 100_000

    # ─── Privacy & Security ───────────────────────────────────────────────────
    privacy_mode: Literal["normal", "strict"] = "normal"

    # ─── Logging ──────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ─── Storage ──────────────────────────────────────────────────────────────
    data_dir: str = "./data"

    # ─── API Server ───────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    # ─── Validation ───────────────────────────────────────────────────────────

    @field_validator("anthropic_api_key", "openai_api_key", mode="before")
    @classmethod
    def _strip_key(cls, v: str) -> str:
        return (v or "").strip()

    @model_validator(mode="after")
    def _validate_api_keys(self) -> Settings:
        """Ensure API key is present for the selected provider."""
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic. "
                "Set it in .env or as an environment variable."
            )
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai. "
                "Set it in .env or as an environment variable."
            )
        return self


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()


def reset_settings() -> None:
    """Clear the cached settings (useful for testing)."""
    get_settings.cache_clear()
