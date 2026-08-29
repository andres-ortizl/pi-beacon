from __future__ import annotations

import os
import tomllib
from pathlib import Path

from platformdirs import user_config_path
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class DisplaySettings(BaseModel):
    show_cost: bool = True
    show_context: bool = True
    show_recent_sessions: bool = True


class PathSettings(BaseModel):
    sessions_dir: Path | None = None
    runtime_dir: Path | None = None
    database: Path | None = None
    socket: Path | None = None


class ServiceSettings(BaseModel):
    runtime_interval_seconds: float = Field(default=2, ge=0.25, le=60)
    history_interval_seconds: float = Field(default=60, ge=1, le=3600)
    history_enabled: bool = True
    history_retention_days: int = Field(default=7, ge=1, le=365)
    watch_debounce_ms: int = Field(default=250, ge=50, le=5000)


class NotificationSettings(BaseModel):
    enabled: bool = True
    settled: bool = True
    waiting: bool = True
    errors: bool = True
    minimum_duration_seconds: int = Field(default=10, ge=0, le=3600)
    sound: bool = False
    sound_file: Path | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PI_BEACON_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    schema_version: int = 1
    recent_session_limit: int = Field(default=5, ge=1, le=20)
    display: DisplaySettings = Field(default_factory=DisplaySettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    service: ServiceSettings = Field(default_factory=ServiceSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return env_settings, init_settings, dotenv_settings, file_secret_settings


def default_config_path() -> Path:
    return user_config_path("pi-beacon") / "config.toml"


def load_settings(path: Path | None = None) -> Settings:
    environment_path = os.environ.get("PI_BEACON_CONFIG")
    config_path = path or (Path(environment_path).expanduser() if environment_path else None)
    config_path = config_path or default_config_path()
    if not config_path.is_file():
        return Settings()
    with config_path.open("rb") as config_file:
        payload = tomllib.load(config_file)
    return Settings(**payload)
