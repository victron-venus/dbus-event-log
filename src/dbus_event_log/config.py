"""Configuration models for dbus-event-log."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageConfig(BaseSettings):
    """Storage backend configuration."""

    model_config = SettingsConfigDict(env_prefix="DBUS_EVENT_LOG_STORAGE_")

    backend: Literal["sqlite", "timescaledb"] = "sqlite"
    sqlite_path: Path = Field(default=Path("/var/lib/dbus-event-log/events.db"))
    timescaledb_dsn: str | None = None
    retention_days: int = 30
    rotation_size_mb: int = 100
    vacuum_on_startup: bool = True


class MQTTConfig(BaseSettings):
    """MQTT broker configuration."""

    model_config = SettingsConfigDict(env_prefix="DBUS_EVENT_LOG_MQTT_")

    enabled: bool = True
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    topic_prefix: str = "victron/dbus/events"
    qos: int = 1
    retain: bool = False
    client_id: str = "dbus-event-log"


class DBusConfig(BaseSettings):
    """D-Bus connection configuration."""

    model_config = SettingsConfigDict(env_prefix="DBUS_EVENT_LOG_DBUS_")

    bus_type: Literal["system", "session"] = "system"
    services: list[str] = Field(
        default_factory=lambda: [
            "com.victronenergy.*",
            "org.freedesktop.Notifications",
            "org.freedesktop.DBus",
        ]
    )
    ignored_signals: list[str] = Field(
        default_factory=lambda: ["NameAcquired", "NameLost"]
    )


class LoggingConfig(BaseSettings):
    """Application logging configuration."""

    model_config = SettingsConfigDict(env_prefix="DBUS_EVENT_LOG_LOG_")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "console"
    file_path: Path | None = None


class Config(BaseSettings):
    """Main application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    storage: StorageConfig = Field(default_factory=StorageConfig)
    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
    dbus: DBusConfig = Field(default_factory=DBusConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        """Load configuration from YAML file."""
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


config = Config()


def get_config() -> Config:
    """Get the current configuration."""
    global config
    return config


def set_config(new_config: Config) -> None:
    """Set the configuration."""
    global config
    config = new_config
