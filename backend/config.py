from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openrouter_api_key: str
    openrouter_default_model: str = "google/gemini-3-flash-preview"
    openrouter_app_title: str = "PM Assistant"
    openrouter_app_url: str = "http://localhost:5173"

    database_url: str = "sqlite+aiosqlite:///./data/pm.db"

    smithery_api_key: str = ""
    smithery_namespace: str = "pm-assistant"
    smithery_api_base: str = "https://api.smithery.ai"

    telegram_bot_token: str | None = None
    telegram_webhook_url: str | None = None

    integrations_config_path: Path = Path(__file__).parent / "integrations.json"

    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
