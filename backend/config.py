from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    openrouter_api_key: str
    openrouter_default_model: str = "google/gemini-3-flash-preview"
    # Comma-separated env (OPENROUTER_FALLBACK_MODELS). When non-empty the
    # agent loop passes [primary, *fallbacks] to OpenRouter so a provider 5xx
    # fails over to the next model instead of killing the turn. Env-only by
    # design — no UI/settings field (CLAUDE.md "model is env-only" invariant).
    # NoDecode: skip pydantic-settings' JSON decoding for this complex-typed
    # field only — the validator below parses the plain CSV string instead.
    openrouter_fallback_models: Annotated[tuple[str, ...], NoDecode] = ()
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

    @field_validator("openrouter_fallback_models", mode="before")
    @classmethod
    def _parse_fallback_models(cls, value: object) -> object:
        """Accept a comma-separated string from env, normalise to a tuple.

        Whitespace around each entry is stripped and blank entries dropped so
        trailing commas / empty values produce an empty tuple, not "" models.
        """
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
