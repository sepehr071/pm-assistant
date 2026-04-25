from __future__ import annotations

from pathlib import Path

import pytest

import config as config_module
from config import Settings, get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_get_settings_returns_cached_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-123")

    s1 = get_settings()
    s2 = get_settings()

    assert s1 is s2
    assert isinstance(s1, Settings)


def test_defaults_applied_when_env_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Redirect env_file lookup to an empty tmp file so defaults kick in for everything except the required key
    empty_env = tmp_path / ".env"
    empty_env.write_text("OPENROUTER_API_KEY=sk-or-test-xyz\n", encoding="utf-8")

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-xyz")
    # Ensure none of the optional env vars are set
    for key in (
        "OPENROUTER_DEFAULT_MODEL",
        "OPENROUTER_APP_TITLE",
        "OPENROUTER_APP_URL",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=str(empty_env))

    assert settings.openrouter_api_key == "sk-or-test-xyz"
    assert settings.openrouter_default_model == "google/gemini-3-flash-preview"
    assert settings.openrouter_app_title == "PM Assistant"
    assert settings.openrouter_app_url == "http://localhost:5173"
    assert settings.database_url == "sqlite+aiosqlite:///./data/pm.db"
    assert settings.integrations_config_path.name == "integrations.json"
    assert settings.smithery_namespace == "pm-assistant"
    assert settings.smithery_api_base == "https://api.smithery.ai"
    assert settings.smithery_api_key == ""


def test_missing_required_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(Exception):
        Settings(_env_file=str(empty_env))


def test_module_exposes_get_settings() -> None:
    assert callable(config_module.get_settings)
